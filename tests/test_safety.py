"""Exercise network validation without importing Discord or contacting services."""
import ast
import ipaddress
import re
import unittest
from pathlib import Path
from urllib.parse import urlparse, parse_qs

source = Path(__file__).resolve().parents[1] / 'bot.py'
tree = ast.parse(source.read_text(encoding='utf-8'))
nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in {'safe_url', 'playlist_url', 'ffmpeg_input_args'}]
namespace = dict(ipaddress=ipaddress, re=re, urlparse=urlparse, parse_qs=parse_qs)
exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), 'exec'), namespace)

class SafetyTests(unittest.TestCase):
    def test_private_aliases_and_credentials_rejected(self):
        for url in ['http://example.com/a.mp3','https://127.0.0.1/a','https://127.1/a','https://2130706433/a','https://0x7f000001/a','https://localhost./a','https://host.local./a','https://[::1]/a','https://user:password@example.com/a','https://example.com:8080/a']:
            self.assertFalse(namespace['safe_url'](url),url)
        for url in ['https://example.com/a.mp3','https://www.youtube.com/watch?v=demo','https://8.8.8.8/a']:
            self.assertTrue(namespace['safe_url'](url),url)

    def test_playlist_domains_require_boundary(self):
        self.assertFalse(namespace['playlist_url']('https://evilsoundcloud.com/sets/a'))
        self.assertTrue(namespace['playlist_url']('https://soundcloud.com/demo/sets/a'))

    def test_headers_cannot_inject_newlines(self):
        args=namespace['ffmpeg_input_args']({'http_headers':{'Cookie':'secret\r\nInjected: yes','User-Agent':'Demo','X-Other':'ignored'}})
        self.assertIn('User-Agent: Demo\r\n',args)
        self.assertNotIn('secret',str(args))

    def test_container_includes_startup_module(self):
        self.assertIn('COPY bot.py server_setup.py .',(source.parent/'Dockerfile').read_text())

if __name__ == '__main__': unittest.main()
