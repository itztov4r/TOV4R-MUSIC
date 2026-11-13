"""Darija text commands for temporary rooms: .v <action>."""
import logging
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
`.v help` — هاد اللائحة

التحكم لمول الروم. Admin ديال Discord كيحتافظ بصلاحياتو.'''

ALIASES={'rename':'name','unhide':'show','allow':'permit','deny':'reject','block':'reject','owner':'info'}

class VoiceCommands(commands.Cog):
    def __init__(self,bot): self.bot=bot

    @commands.command(name='v',aliases=['voice'],ignore_extra=False)
    @commands.guild_only()
    @commands.cooldown(1,2,commands.BucketType.member)
    async def voice(self,ctx,action:str='help',*,argument:str=''):
        action=ALIASES.get(action.casefold(),action.casefold())
        if action=='help':
            await ctx.send(embed=discord.Embed(title='Tov4r • أوامر الرومات',description=HELP,color=0xef3340)); return
        known={'lock','unlock','hide','show','name','limit','permit','reject','kick','transfer','claim','info'}
        if action not in known: raise commands.BadArgument('هاد الأمر ما كاينش. كتب `.v help` باش تشوف الأوامر.')
        if action not in {'name','limit','permit','reject','kick','transfer'} and argument:
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
                elif action=='permit':
                    await channel.set_permissions(target,view_channel=True,connect=True,reason=reason)
                    answer=f'✅ {target.mention} يقدر يدخل دابا.'
                else:
                    if target.guild_permissions.administrator: raise commands.BadArgument('ما تقدرش تخرج ولا تمنع Admin بهاد الأمر.')
                    inside=target.voice and target.voice.channel==channel
                    if action=='kick' and not inside: raise commands.BadArgument('هاد العضو ماشي داخل الروم ديالك.')
                    if action=='reject': await channel.set_permissions(target,connect=False,reason=reason)
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
