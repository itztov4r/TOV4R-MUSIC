import asyncio
import logging
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

import bot as music


@pytest.fixture(autouse=True)
def isolated_state():
    music.states.clear()
    yield
    for state in music.states.values():
        if state.idle_task:
            state.idle_task.cancel()
    music.states.clear()


class Voice:
    def __init__(self):
        self.channel = None
        self.playing = False
        self.paused = False
        self.connected = True
        self.play = MagicMock(side_effect=self.start)
        self.stop = MagicMock(side_effect=self.halt)
        self.disconnect = AsyncMock()

    def start(self, source, *, after):
        self.playing = True
        self.after = after

    def halt(self):
        self.playing = False

    def is_connected(self):
        return self.connected

    def is_playing(self):
        return self.playing

    def is_paused(self):
        return self.paused


def interaction():
    response = SimpleNamespace(done=False)

    async def defer(**kwargs):
        response.done = True

    response.defer = AsyncMock(side_effect=defer)
    response.is_done = lambda: response.done
    response.send_message = AsyncMock()
    user = MagicMock(spec=discord.Member)
    user.voice = SimpleNamespace(channel=SimpleNamespace(connect=AsyncMock()))
    return SimpleNamespace(
        response=response, followup=SimpleNamespace(send=AsyncMock()),
        guild=SimpleNamespace(id=10, voice_client=None), user=user, channel=None,
    )


def track(name='test'):
    return music.Track(name, 'https://example.com/music.mp3')


def test_container_includes_all_runtime_modules(tmp_path):
    root = Path(__file__).resolve().parents[1]
    # Emulate the Python source COPY from the actual image recipe. This caught
    # the production crash even though importing from the checkout worked.
    for line in (root / 'Dockerfile').read_text().splitlines():
        if line.startswith('COPY ') and line.endswith(' .'):
            for source in line.split()[1:-1]:
                if source.endswith('.py'):
                    shutil.copy(root / source, tmp_path / source)
    result = subprocess.run(
        [sys.executable, '-c', 'import bot; import server_setup; print("runtime OK")'],
        cwd=tmp_path, capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.asyncio
async def test_cold_start_registers_commands_without_network(monkeypatch):
    monkeypatch.setattr(music, 'GUILD_ID', '')
    monkeypatch.setattr(music.bot.tree, 'sync', AsyncMock(return_value=[]))
    async with music.bot:
        await music.bot.setup_hook()
        names = {command.name for command in music.bot.tree.get_commands()}
        assert {'sma3', 'testaudio', 'testffmpeg', 'server'} <= names


@pytest.mark.asyncio
async def test_slow_voice_join_is_acknowledged_first():
    it = interaction()
    voice = Voice()

    async def connect(**kwargs):
        assert it.response.is_done(), 'voice network work preceded Discord acknowledgement'
        return voice

    it.user.voice.channel.connect.side_effect = connect
    state = await music.ensure_voice(it)
    assert state.voice is voice
    it.response.defer.assert_awaited_once()


@pytest.mark.asyncio
async def test_failed_voice_join_uses_followup():
    it = interaction()
    it.user.voice.channel.connect.side_effect = asyncio.TimeoutError
    assert await music.ensure_voice(it) is None
    it.followup.send.assert_awaited_once()
    it.response.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_concurrent_joins_connect_only_once():
    first, second = interaction(), interaction()
    second.guild = first.guild
    second.user.voice = first.user.voice
    voice = Voice()
    voice.channel = first.user.voice.channel

    async def connect(**kwargs):
        await asyncio.sleep(0)
        first.guild.voice_client = voice
        return voice

    first.user.voice.channel.connect.side_effect = connect
    await asyncio.gather(music.ensure_voice(first), music.ensure_voice(second))
    first.user.voice.channel.connect.assert_awaited_once()


def test_queue_limit_is_rechecked_after_extraction():
    state = music.MusicState(voice=Voice())
    state.queue.extend([track()] * 78)
    assert len(music.enqueue_tracks(state, [track()] * 5, state.generation)) == 2
    with pytest.raises(ValueError, match='Queue'):
        music.enqueue_tracks(state, [track()], state.generation)
    assert len(state.queue) == music.MAX_QUEUE


def test_stopped_lookup_cannot_repopulate_queue():
    state = music.MusicState(voice=Voice())
    old_generation = state.generation
    music.invalidate_playback(state)
    with pytest.raises(ValueError, match='annulée'):
        music.enqueue_tracks(state, [track()], old_generation)
    assert not state.queue


def test_priority_playlist_retains_order():
    state = music.MusicState(voice=Voice())
    state.queue.append(track('old'))
    music.enqueue_tracks(state, [track('first'), track('second')], 0, priority=True)
    assert [item.title for item in state.queue] == ['first', 'second', 'old']


@pytest.mark.asyncio
async def test_idle_disconnect_does_not_cancel_itself(monkeypatch):
    state = music.state_for(10)
    state.voice = Voice()
    voice = state.voice
    monkeypatch.setattr(music.asyncio, 'sleep', AsyncMock())
    state.idle_task = asyncio.create_task(music.idle_disconnect(10))
    await state.idle_task
    voice.disconnect.assert_awaited_once()
    assert state.voice is None


@pytest.mark.asyncio
async def test_dj_queue_edit_does_not_wait_for_media_preflight(monkeypatch):
    state = music.state_for(10)
    state.queue.extend([track(), track()])
    monkeypatch.setattr(music, 'require_dj', AsyncMock(return_value=True))
    monkeypatch.setattr(music, 'member_state', AsyncMock(return_value=state))
    it = interaction()
    async with state.lock:
        await asyncio.wait_for(music.djclear.callback(it), 0.5)
    assert not state.queue
    it.response.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_stop_during_stream_preflight_cancels_playback(monkeypatch):
    state = music.state_for(10)
    state.voice = Voice()
    state.queue.append(track())
    started, release = asyncio.Event(), asyncio.Event()

    async def preflight(*args):
        started.set()
        await release.wait()
        return {'url': 'https://example.com/music.mp3'}, 'OK'

    monkeypatch.setattr(music.asyncio, 'to_thread', preflight)
    task = asyncio.create_task(music.advance(10))
    await started.wait()
    music.invalidate_playback(state)
    release.set()
    await task
    state.voice.play.assert_not_called()
    assert state.current is None


@pytest.mark.asyncio
async def test_duplicate_advance_does_not_consume_next_track():
    state = music.state_for(10)
    state.voice = Voice()
    state.voice.playing = True
    state.current = track('current')
    state.queue.append(track('next'))
    await asyncio.gather(music.advance(10), music.advance(10))
    assert state.current.title == 'current'
    assert state.queue[0].title == 'next'


@pytest.mark.asyncio
@pytest.mark.parametrize('manual_skip', [False, True])
async def test_failed_looped_stream_moves_on(monkeypatch, manual_skip):
    state = music.state_for(10)
    state.voice = Voice()
    state.channel = SimpleNamespace(send=AsyncMock())
    state.current = track('broken')
    state.loop = True
    state.queue.append(track('working'))
    played = asyncio.Event()
    calls = []

    async def preflight(fn, item):
        calls.append(item.title)
        return {'url': item.page_url}, 'OK'

    class Source(discord.AudioSource):
        pcm_bytes = 0

        def __init__(self, *args, **kwargs):
            pass

        def read(self):
            return b''

    monkeypatch.setattr(music.asyncio, 'to_thread', preflight)
    monkeypatch.setattr(music, 'MonitoredFFmpegAudio', Source)
    await music.advance(10)
    original_start = state.voice.start

    def second_start(source, *, after):
        original_start(source, after=after)
        played.set()

    state.voice.play.side_effect = second_start
    state.voice.playing = False
    state.skip_requested = manual_skip
    state.voice.after(None)
    await asyncio.wait_for(played.wait(), 2)
    assert calls == ['broken', 'working']
    assert state.current.title == 'working'
    if manual_skip:
        assert all('STREAM AUDIO MA KHEDMATCH' not in call.kwargs['embed'].title
                   for call in state.channel.send.call_args_list)


@pytest.mark.parametrize('url', [
    'http://example.com/a.mp3', 'https://localhost/a.mp3', 'https://localhost./a',
    'https://127.0.0.1/a.mp3', 'https://[::1]/a', 'https://10.0.0.2/a',
    'https://service.internal/a', 'https://2130706433/a',
    'https://user:password@example.com/a', 'https://example.com:99999/a',
])
def test_unsafe_urls_are_rejected(url):
    assert not music.safe_url(url)


def test_redaction_covers_errors_and_tracebacks(monkeypatch):
    monkeypatch.setattr(music, 'TOKEN', 'test-private-bot-token')
    error = ValueError('test-private-bot-token https://example.com/a?signature=private')
    assert 'private' not in music.public_error(error)
    record = logging.LogRecord('test', logging.ERROR, __file__, 1, '%s', (error,), None)
    output = music.RedactingFormatter('%(message)s').format(record)
    assert 'private' not in output


def test_sqlite_creates_parent_and_closes_connection(tmp_path, monkeypatch):
    monkeypatch.setattr(music, 'DB_PATH', str(tmp_path / 'data' / 'dj.sqlite3'))
    with music.open_db() as conn:
        conn.execute('INSERT INTO dj_access VALUES (42, 9999999999)')
        conn.commit()
    assert music.get_access(42) == (True, 9999999999)
    import sqlite3
    with pytest.raises(sqlite3.ProgrammingError):
        conn.execute('SELECT 1')


def test_real_ffmpeg_decodes_stereo_pcm():
    source = discord.FFmpegPCMAudio(
        'sine=frequency=440:duration=0.1', before_options='-f lavfi',
        options='-vn -loglevel error', executable=music.FFMPEG,
    )
    try:
        frames = []
        while frame := source.read():
            frames.append(frame)
        assert len(frames) >= 4
        assert all(len(frame) == 3840 for frame in frames)
        assert any(any(frame) for frame in frames)
    finally:
        source.cleanup()


def test_name_search_uses_soundcloud_first(monkeypatch):
    calls = []
    def extract(query, **kwargs):
        calls.append(query)
        return {'entries': [{'title': 'Song', 'webpage_url': 'https://soundcloud.com/artist/song'}]}
    monkeypatch.setattr(music, 'extract_sync', extract)
    tracks = music.lookup_tracks_sync('Song', 'listener', 1)
    assert calls == ['scsearch3:Song']
    assert tracks[0].search_fallback == 'Song'


def test_search_provider_failure_uses_next_provider(monkeypatch):
    def extract(query, **kwargs):
        if query.startswith('scsearch'):
            raise ValueError('Unavailable')
        return {'entries': [{'title': 'Song', 'webpage_url': 'https://youtube.com/watch?v=abcdefghijk'}]}
    monkeypatch.setattr(music, 'extract_sync', extract)
    assert music.lookup_tracks_sync('Song', 'listener', 1)[0].page_url.startswith('https://youtube.com')


@pytest.mark.parametrize('extract_failure', [True, False])
def test_search_retries_after_extraction_or_pcm_failure(monkeypatch, extract_failure):
    first = music.Track(title='Old', page_url='https://soundcloud.com/a/old', requester='listener', search_fallback='Song')
    second = music.Track(title='Actual song', page_url='https://soundcloud.com/a/new', requester='listener')
    def probe(track):
        if track.page_url == first.page_url:
            if extract_failure:
                raise ValueError('Sign in to confirm you are not a bot')
            return {}, 'HTTP 403'
        return {'url': 'https://example.com/audio.mp3', 'title': 'Actual recording', 'duration': 120}, 'PCM OK'
    monkeypatch.setattr(music, 'probe_track_audio_sync', probe)
    monkeypatch.setattr(music, 'search_tracks_sync', lambda *args: [second])
    info, message = music.playable_audio_sync(first)
    assert info and message == 'PCM OK'
    assert first.title == 'Actual recording'
    assert first.page_url == second.page_url
    assert first.duration == 120


def test_explicit_link_not_replaced_by_different_recording(monkeypatch):
    track = music.Track(title='Song', page_url='https://youtube.com/watch?v=abcdefghijk', requester='listener')
    monkeypatch.setattr(music, 'probe_track_audio_sync', lambda _: ({}, 'blocked'))
    search = MagicMock()
    monkeypatch.setattr(music, 'search_tracks_sync', search)
    assert music.playable_audio_sync(track) == ({}, 'blocked')
    search.assert_not_called()


def test_youtube_auth_error_is_actionable():
    message = music.public_error(ValueError('Sign in to confirm you’re not a bot. Use --cookies'))
    assert 'SoundCloud' in message
    assert '--cookies' not in message


def test_search_rejects_wrong_artist(monkeypatch):
    def extract(query, **kwargs):
        if query.startswith('scsearch'):
            return {'entries': [{'title': 'Desperado', 'uploader': 'Unrelated artist', 'webpage_url': 'https://soundcloud.com/other/desperado'}]}
        return {'entries': [{'title': 'Stormy - Desperado', 'webpage_url': 'https://youtube.com/watch?v=abcdefghijk'}]}
    monkeypatch.setattr(music, 'extract_sync', extract)
    assert music.lookup_tracks_sync('Stormy Desperado', 'listener', 1)[0].title == 'Stormy - Desperado'
