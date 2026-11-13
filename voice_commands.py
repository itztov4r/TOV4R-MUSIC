"""Darija text commands for temporary rooms: .v <action>."""
import logging
import re
import discord
from discord.ext import commands

HELP = '''دخل لـ **➕ Create your room** باش تصايب روم ديالك، وبقى داخلها وانت كتستعمل الأوامر.

`.v lock` — سدّ الدخول للروم
`.v unlock` — حلّ الدخول
`.v hide` — خبّي الروم
`.v show` — بيّن الروم
`.v name سميّة جديدة` — بدّل السميّة
`.v limit 5` — حدّد عدد الناس؛ `0` بلا حد
`.v permit @عضو` — سمح ليه يدخل، حتى إلا كانت مسدودة
`.v reject @عضو` — خرجو ومنعو يرجع
`.v kick @عضو` — خرجو فقط، يقدر يرجع إلا الدخول مسموح
`.v transfer @عضو` — عطيو ملكية الروم (خاصو يكون داخلها)
`.v claim` — خذ الملكية إلا المالك خرج
`.v info` — شوف معلومات الروم
`.v members` — شكون داخل الروم
`.v permits` / `.v rejects` — لائحة المسموحين / الممنوعين
`.v unpermit @عضو` — حيد السماح الخاص
`.v unreject @عضو` — حيد المنع الخاص
`.v bitrate 64` — جودة الصوت بـ kbps، حسب حد السيرفر
`.v region auto` — اتصال أوتوماتيكي؛ تقدر تكتب مثلاً `rotterdam`
`.v chat on` / `.v chat off` — حلّ / سدّ شات الروم
`.v invite` — رابط الروم (ما كيبدلش صلاحيات الدخول)
`.v reset` — رجّع إعدادات الروم؛ كيطلب تأكيد
`.v delete` — مسح الروم وإخراج اللي فيها؛ كيطلب تأكيد
`.v help` — هاد اللائحة

التحكم لمول الروم. Admin ديال Discord كيحتافظ بصلاحياتو.'''

ALIASES={'rename':'name','unhide':'show','allow':'permit','deny':'reject','block':'reject','owner':'info','unblock':'unreject','remove':'delete','users':'members','link':'invite','bit':'bitrate','text':'chat'}

class VoiceCommands(commands.Cog):
    def __init__(self,bot): self.bot=bot

    @commands.command(name='v',aliases=['voice'],ignore_extra=False)
    @commands.guild_only()
    @commands.cooldown(1,2,commands.BucketType.member)
    async def voice(self,ctx,action:str='help',*,argument:str=''):
        action=ALIASES.get(action.casefold(),action.casefold())
        if action=='help':
            await ctx.send(embed=discord.Embed(title='Tov4r • أوامر الرومات',description=HELP,color=0xef3340)); return
        known={'lock','unlock','hide','show','name','limit','permit','reject','kick','transfer','claim','info','members','permits','rejects','unpermit','unreject','bitrate','region','chat','invite','reset','delete'}
        if action not in known: raise commands.BadArgument('هاد الأمر ما كاينش. كتب `.v help` باش تشوف الأوامر.')
        if action not in {'name','limit','permit','reject','kick','transfer','unpermit','unreject','bitrate','region','chat','reset','delete'} and argument:
            raise commands.BadArgument(f'هاد الأمر كيتكتب بوحدو: `.v {action}`')
        channel=ctx.author.voice.channel if ctx.author.voice else None
        if not channel: raise commands.BadArgument('خاصك تدخل للروم ديالك لول. باش تصايب وحدة، دخل لـ ➕ Create your room.')
        async with self.bot.locks[('room',channel.id)]:
            # Check again after waiting on another operation.
            if not ctx.author.voice or ctx.author.voice.channel!=channel:
                raise commands.BadArgument('بدّلتي الروم. عاود الأمر وانت داخل الروم ديالك.')
            room=self.bot.store.one('SELECT * FROM rooms WHERE channel=? AND guild=?',(channel.id,ctx.guild.id))
            if not room: raise commands.BadArgument('هاد الروم ماشي من الرومات المؤقتة ديالي.')
            if action=='info':
                await ctx.send(f'🔊 **{discord.utils.escape_markdown(channel.name)}**\n👑 المالك: <@{room["owner"]}>\n👥 الحد: {channel.user_limit or "بلا حد"}'); return
            if action in {'members','invite'}:
                if action=='invite':
                    await ctx.send(f'🔗 https://discord.com/channels/{ctx.guild.id}/{channel.id} — الدخول حسب صلاحيات الروم.')
                else:
                    lines=[f'<@{m.id}>' for m in channel.members]
                    await ctx.send(embed=discord.Embed(title=f'👥 الأعضاء ({len(lines)})',description='\n'.join(lines[:100]) or 'الروم خاوية.',color=0xef3340))
                return
            if action!='claim' and room['owner']!=ctx.author.id:
                raise commands.BadArgument('غير مول الروم يقدر يدير هاد الأمر. إلا المالك خرج، جرّب `.v claim`.')
            reason=f'Tov4r .v {action} by {ctx.author.id}'
            if action in {'lock','unlock','hide','show'}:
                ow=channel.overwrites_for(ctx.guild.default_role)
                if action in {'lock','unlock'}: ow.connect=action=='unlock'
                else: ow.view_channel=action=='show'
                await channel.set_permissions(ctx.guild.default_role,overwrite=ow,reason=reason)
                answer={'lock':'🔒 تسدّات الروم. اللي عطيتو Permit يقدر يبقى يدخل.','unlock':'🔓 تحلّات الروم.','hide':'👻 تخبّات الروم.','show':'👁 الروم ولات باينة.'}[action]
            elif action=='name':
                name=argument.strip()
                if not 1<=len(name)<=80: raise commands.BadArgument('كتب سميّة بين 1 و80 حرف، بحال `.v name جلسة صحاب`.')
                await channel.edit(name=name,reason=reason)
                answer='✏ تبدّلات سميّة الروم.'
            elif action=='limit':
                if not argument.isdecimal() or not 0<=int(argument)<=99:
                    raise commands.BadArgument('كتب عدد من 0 حتى 99، بحال `.v limit 5`. صفر كيعني بلا حد.')
                await channel.edit(user_limit=int(argument),reason=reason)
                answer='👥 تبدّل الحد لـ '+('بلا حد.' if int(argument)==0 else argument+' أعضاء.')
            elif action in {'permits','rejects'}:
                ids=[target.id for target,ow in channel.overwrites.items()
                     if not isinstance(target,discord.Role) and target.id not in {room['owner'],self.bot.user.id}
                     and ow.connect is (action=='permits')]
                await ctx.send(embed=discord.Embed(title='✅ المسموحين' if action=='permits' else '⛔ الممنوعين',
                    description='\n'.join(f'<@{uid}>' for uid in ids[:100]) or 'اللائحة خاوية.',color=0xef3340))
                return
            elif action=='bitrate':
                maximum=int(ctx.guild.bitrate_limit)//1000
                if not argument.isdecimal() or not 8<=int(argument)<=maximum:
                    raise commands.BadArgument(f'كتب جودة بين 8 و{maximum} kbps، بحال `.v bitrate 64`.')
                await channel.edit(bitrate=int(argument)*1000,reason=reason)
                answer=f'🎧 جودة الصوت ولات {argument} kbps.'
            elif action=='region':
                region=argument.strip().lower()
                if not re.fullmatch(r'[a-z][a-z0-9-]{1,31}',region):
                    raise commands.BadArgument('استعمل `.v region auto` ولا اسم منطقة بحال `rotterdam`.')
                await channel.edit(rtc_region=None if region=='auto' else region,reason=reason)
                answer='🌍 تبدّلات منطقة الاتصال.'
            elif action=='chat':
                if argument.lower() not in {'on','off'}: raise commands.BadArgument('استعمل `.v chat on` ولا `.v chat off`.')
                me=ctx.guild.me
                bot_ow=channel.overwrites_for(me); bot_ow.send_messages=True
                await channel.set_permissions(me,overwrite=bot_ow,reason=reason)
                ow=channel.overwrites_for(ctx.guild.default_role); ow.send_messages=argument.lower()=='on'
                await channel.set_permissions(ctx.guild.default_role,overwrite=ow,reason=reason)
                answer='💬 تحلّ شات الروم.' if argument.lower()=='on' else '🔇 تسدّ شات الروم.'
            elif action in {'reset','delete'}:
                # Require the current room ID: an old confirmation cannot affect a different room.
                if argument!=f'confirm {channel.id}':
                    effect='الروم غادي تتمسح واللي فيها غادي يخرجو.' if action=='delete' else 'غادي تتحل وتبان الروم، وتتحيد استثناءات الدخول والحد والجودة والمنطقة المخصصة.'
                    await ctx.send(f'⚠️ {effect}\nللتأكيد كتب `.v {action} confirm {channel.id}`')
                    return
                if action=='delete':
                    await channel.delete(reason=reason)
                    self.bot.store.run('DELETE FROM rooms WHERE channel=?',(channel.id,))
                    # The invoking text channel might itself have just been deleted.
                    if ctx.channel.id!=channel.id: await ctx.send('🗑 تمسحات الروم.')
                    return
                overwrites=channel.overwrites
                for target,ow in list(overwrites.items()):
                    if isinstance(target,discord.Role) and target!=ctx.guild.default_role: continue
                    if target.id in {room['owner'],self.bot.user.id}: continue
                    ow.connect=None; ow.view_channel=None
                    if target==ctx.guild.default_role:
                        ow.connect=True; ow.view_channel=True; ow.send_messages=None
                    if ow.is_empty(): del overwrites[target]
                    else: overwrites[target]=ow
                await channel.edit(overwrites=overwrites,user_limit=0,bitrate=min(64000,int(ctx.guild.bitrate_limit)),rtc_region=None,reason=reason)
                answer='♻️ رجعات إعدادات الدخول والصوت. السميّة والملكية بقاو كيف كانو.'
            elif action=='claim':
                if room['owner']==ctx.author.id: raise commands.BadArgument('نتا أصلاً مول الروم ✅')
                if any(m.id==room['owner'] for m in channel.members):
                    raise commands.BadArgument('مول الروم مازال داخل. ما تقدرش تاخذ الملكية.')
                if self.bot.store.one('SELECT channel FROM rooms WHERE guild=? AND owner=?',(ctx.guild.id,ctx.author.id)):
                    raise commands.BadArgument('عندك أصلاً روم ديالك.')
                old=ctx.guild.get_member(room['owner'])
                await channel.set_permissions(ctx.author,view_channel=True,connect=True,reason=reason)
                if old: await channel.set_permissions(old,overwrite=None,reason=reason)
                self.bot.store.run('UPDATE rooms SET owner=? WHERE channel=?',(ctx.author.id,channel.id))
                answer='👑 دابا نتا مول الروم.'
            else:
                if not argument: raise commands.BadArgument(f'خاصك تمنشن العضو: `.v {action} @عضو`')
                target=await commands.MemberConverter().convert(ctx,argument)
                if target.bot or target.id==ctx.author.id: raise commands.BadArgument('اختار عضو آخر، ماشي نتا ولا بوت.')
                if action=='transfer':
                    if not target.voice or target.voice.channel!=channel: raise commands.BadArgument('هاد العضو خاصو يكون معاك فالروم.')
                    if self.bot.store.one('SELECT channel FROM rooms WHERE guild=? AND owner=?',(ctx.guild.id,target.id)):
                        raise commands.BadArgument('هاد العضو عندو أصلاً روم ديالو.')
                    await channel.set_permissions(target,view_channel=True,connect=True,reason=reason)
                    await channel.set_permissions(ctx.author,overwrite=None,reason=reason)
                    self.bot.store.run('UPDATE rooms SET owner=? WHERE channel=?',(target.id,channel.id))
                    answer=f'👑 عطيت ملكية الروم لـ {target.mention}.'
                elif action in {'unpermit','unreject'}:
                    ow=channel.overwrites_for(target)
                    if action=='unpermit':
                        if ow.connect is True: ow.connect=None
                        if ow.view_channel is True: ow.view_channel=None
                    elif ow.connect is False: ow.connect=None
                    await channel.set_permissions(target,overwrite=None if ow.is_empty() else ow,reason=reason)
                    answer='✅ تحيد الاستثناء. الدخول دابا حسب صلاحيات السيرفر والروم.'
                elif action=='permit':
                    ow=channel.overwrites_for(target); ow.view_channel=True; ow.connect=True
                    await channel.set_permissions(target,overwrite=ow,reason=reason)
                    answer=f'✅ {target.mention} يقدر يدخل دابا.'
                else:
                    if target.guild_permissions.administrator: raise commands.BadArgument('ما تقدرش تخرج ولا تمنع Admin بهاد الأمر.')
                    inside=target.voice and target.voice.channel==channel
                    if action=='kick' and not inside: raise commands.BadArgument('هاد العضو ماشي داخل الروم ديالك.')
                    if action=='reject':
                        ow=channel.overwrites_for(target); ow.connect=False
                        await channel.set_permissions(target,overwrite=ow,reason=reason)
                    if inside: await target.move_to(None,reason=reason)
                    answer=f'⛔ {target.mention} تمنع من الروم. `.v permit` باش ترجع تسمح ليه.' if action=='reject' else f'👋 خرجت {target.mention} من الروم.'
            await ctx.send(answer)

    async def cog_command_error(self,ctx,error):
        original=getattr(error,'original',error)
        if isinstance(error,commands.CommandOnCooldown): message=f'تسنّى {error.retry_after:.0f} ثواني وعاود.'
        elif isinstance(error,commands.MemberNotFound): message='ما لقيتش هاد العضو. منشنو بـ @ ولا كتب ID ديالو.'
        elif isinstance(error,commands.NoPrivateMessage): message='هاد الأوامر خدامين غير داخل السيرفر.'
        elif isinstance(error,commands.BadArgument): message=str(error)
        elif isinstance(original,discord.Forbidden): message='ما عنديش الصلاحيات. Admin خاصو يراجع Manage Channels وMove Members وصلاحيات الروم.'
        elif isinstance(original,discord.HTTPException): message='Discord ما قبلش التغيير دابا. تسنّى شوية وعاود، خصوصاً إلا بدّلتي السميّة بزاف.'
        else:
            logging.getLogger('tov4r').error('Voice text command failed',exc_info=original)
            message='وقع مشكل فالأمر. عاود جرّب ولا عيّط للـAdmin.'
        await ctx.send(message)
