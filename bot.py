import discord
from discord.ext import commands, tasks
import json
import os
import asyncio
from datetime import datetime, timedelta
import re

# Bot setup
intents = discord.Intents.default()
intents.members = True
intents.message_content = True
intents.guilds = True
intents.voice_states = True

bot = commands.Bot(command_prefix='!', intents=intents)

# Configuration file
CONFIG_FILE = 'config.json'

def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r') as f:
            return json.load(f)
    return {
        'autorole': {},
        'greetings': {},
        'logs': {},
        'automod': {
            'enabled': False,
            'banned_words': [],
            'spam_threshold': 5,
            'spam_time': 10
        },
        'antinuke': {
            'enabled': False,
            'ban_threshold': 5,
            'kick_threshold': 5,
            'role_threshold': 5,
            'time_window': 60
        }
    }

def save_config(config):
    with open(CONFIG_FILE, 'w') as f:
        json.dump(config, f, indent=4)

config = load_config()

# Antinuke tracking
antinuke_data = {}

# Automod tracking
automod_data = {}

@bot.event
async def on_ready():
    print(f'Bot is ready. Logged in as {bot.user}')
    check_antinuke.start()

@tasks.loop(seconds=60)
async def check_antinuke():
    current_time = datetime.now()
    for guild_id, data in antinuke_data.items():
        for user_id, actions in data.items():
            actions = [action for action in actions if current_time - action['time'] < timedelta(seconds=config['antinuke']['time_window'])]
            if len(actions) >= config['antinuke']['ban_threshold'] and any(a['type'] == 'ban' for a in actions):
                guild = bot.get_guild(int(guild_id))
                if guild:
                    member = guild.get_member(int(user_id))
                    if member:
                        await member.ban(reason="Antinuke: Mass banning detected")
                        log_channel = config['logs'].get(str(guild_id))
                        if log_channel:
                            channel = bot.get_channel(int(log_channel))
                            if channel:
                                await channel.send(f"**Antinuke Alert:** {member.mention} was banned for mass banning.")
            # Similar checks for kick and role creation/deletion
            antinuke_data[guild_id][user_id] = actions

@bot.event
async def on_member_join(member):
    # Autorole
    if str(member.guild.id) in config['autorole']:
        role_id = config['autorole'][str(member.guild.id)]
        role = member.guild.get_role(int(role_id))
        if role:
            await member.add_roles(role)
    
    # Greeting
    if str(member.guild.id) in config['greetings']:
        channel_id = config['greetings'][str(member.guild.id)]['channel']
        message = config['greetings'][str(member.guild.id)]['message']
        channel = bot.get_channel(int(channel_id))
        if channel:
            await channel.send(message.replace('{user}', member.mention))

@bot.event
async def on_member_remove(member):
    log_channel = config['logs'].get(str(member.guild.id))
    if log_channel:
        channel = bot.get_channel(int(log_channel))
        if channel:
            embed = discord.Embed(title="Member Left", color=0xff0000)
            embed.add_field(name="User", value=f"{member} ({member.id})", inline=False)
            embed.set_footer(text=f"Timestamp: {datetime.now()}")
            await channel.send(embed=embed)

@bot.event
async def on_member_update(before, after):
    log_channel = config['logs'].get(str(before.guild.id))
    if log_channel:
        channel = bot.get_channel(int(log_channel))
        if channel:
            if before.roles != after.roles:
                added_roles = [role for role in after.roles if role not in before.roles]
                removed_roles = [role for role in before.roles if role not in after.roles]
                embed = discord.Embed(title="Role Update", color=0x00ff00)
                embed.add_field(name="User", value=f"{after} ({after.id})", inline=False)
                if added_roles:
                    embed.add_field(name="Added Roles", value=", ".join([role.name for role in added_roles]), inline=False)
                if removed_roles:
                    embed.add_field(name="Removed Roles", value=", ".join([role.name for role in removed_roles]), inline=False)
                embed.set_footer(text=f"Timestamp: {datetime.now()}")
                await channel.send(embed=embed)

@bot.event
async def on_voice_state_update(member, before, after):
    log_channel = config['logs'].get(str(member.guild.id))
    if log_channel:
        channel = bot.get_channel(int(log_channel))
        if channel:
            if before.channel != after.channel:
                embed = discord.Embed(title="Voice Channel Update", color=0x0000ff)
                embed.add_field(name="User", value=f"{member} ({member.id})", inline=False)
                if before.channel:
                    embed.add_field(name="Left Channel", value=before.channel.name, inline=False)
                if after.channel:
                    embed.add_field(name="Joined Channel", value=after.channel.name, inline=False)
                embed.set_footer(text=f"Timestamp: {datetime.now()}")
                await channel.send(embed=embed)

@bot.event
async def on_message(message):
    if message.author == bot.user:
        return
    
    # Automod
    if config['automod']['enabled'] and str(message.guild.id) in config['logs']:
        # Banned words
        for word in config['automod']['banned_words']:
            if re.search(r'\b' + re.escape(word) + r'\b', message.content, re.IGNORECASE):
                await message.delete()
                await message.channel.send(f"{message.author.mention}, your message contained a banned word.")
                return
        
        # Spam detection
        user_id = str(message.author.id)
        guild_id = str(message.guild.id)
        if guild_id not in automod_data:
            automod_data[guild_id] = {}
        if user_id not in automod_data[guild_id]:
            automod_data[guild_id][user_id] = []
        
        automod_data[guild_id][user_id].append(datetime.now())
        recent_messages = [msg_time for msg_time in automod_data[guild_id][user_id] if datetime.now() - msg_time < timedelta(seconds=config['automod']['spam_time'])]
        automod_data[guild_id][user_id] = recent_messages
        
        if len(recent_messages) > config['automod']['spam_threshold']:
            await message.author.timeout(timedelta(minutes=5), reason="Spam detected")
            await message.channel.send(f"{message.author.mention} has been timed out for spamming.")
            automod_data[guild_id][user_id] = []
    
    await bot.process_commands(message)

@bot.event
async def on_guild_role_create(role):
    if config['antinuke']['enabled']:
        await track_action(role.guild.id, role.guild.owner.id if role.guild.owner else 0, 'role_create')

@bot.event
async def on_guild_role_delete(role):
    if config['antinuke']['enabled']:
        await track_action(role.guild.id, role.guild.owner.id if role.guild.owner else 0, 'role_delete')

@bot.event
async def on_member_ban(guild, user):
    if config['antinuke']['enabled']:
        # Note: This event doesn't provide the banner, so we'd need audit logs for full tracking
        pass

async def track_action(guild_id, user_id, action_type):
    if str(guild_id) not in antinuke_data:
        antinuke_data[str(guild_id)] = {}
    if str(user_id) not in antinuke_data[str(guild_id)]:
        antinuke_data[str(guild_id)][str(user_id)] = []
    
    antinuke_data[str(guild_id)][str(user_id)].append({'type': action_type, 'time': datetime.now()})

# Moderation Commands
@bot.command()
@commands.has_permissions(ban_members=True)
async def ban(ctx, member: discord.Member, *, reason="No reason provided"):
    await member.ban(reason=reason)
    await ctx.send(f"{member} has been banned. Reason: {reason}")

@bot.command()
@commands.has_permissions(kick_members=True)
async def kick(ctx, member: discord.Member, *, reason="No reason provided"):
    await member.kick(reason=reason)
    await ctx.send(f"{member} has been kicked. Reason: {reason}")

@bot.command()
@commands.has_permissions(moderate_members=True)
async def timeout(ctx, member: discord.Member, duration: int, *, reason="No reason provided"):
    await member.timeout(timedelta(minutes=duration), reason=reason)
    await ctx.send(f"{member} has been timed out for {duration} minutes. Reason: {reason}")

@bot.command()
@commands.has_permissions(moderate_members=True)
async def mute(ctx, member: discord.Member, *, reason="No reason provided"):
    # Assuming a muted role exists
    muted_role = discord.utils.get(ctx.guild.roles, name="Muted")
    if muted_role:
        await member.add_roles(muted_role, reason=reason)
        await ctx.send(f"{member} has been muted. Reason: {reason}")
    else:
        await ctx.send("Muted role not found. Please create a 'Muted' role.")

@bot.command()
@commands.has_permissions(ban_members=True)
async def unban(ctx, user_id: int):
    user = await bot.fetch_user(user_id)
    await ctx.guild.unban(user)
    await ctx.send(f"{user} has been unbanned.")

@bot.command()
@commands.has_permissions(moderate_members=True)
async def unmute(ctx, member: discord.Member):
    muted_role = discord.utils.get(ctx.guild.roles, name="Muted")
    if muted_role and muted_role in member.roles:
        await member.remove_roles(muted_role)
        await ctx.send(f"{member} has been unmuted.")
    else:
        await ctx.send(f"{member} is not muted.")

@bot.command()
@commands.has_permissions(moderate_members=True)
async def remove_timeout(ctx, member: discord.Member):
    await member.timeout(None)
    await ctx.send(f"Timeout removed for {member}.")

# Setup Commands
@bot.command()
@commands.has_permissions(administrator=True)
async def set_autorole(ctx, role: discord.Role):
    config['autorole'][str(ctx.guild.id)] = str(role.id)
    save_config(config)
    await ctx.send(f"Autorole set to {role.name}.")

@bot.command()
@commands.has_permissions(administrator=True)
async def set_greeting(ctx, channel: discord.TextChannel, *, message):
    config['greetings'][str(ctx.guild.id)] = {'channel': str(channel.id), 'message': message}
    save_config(config)
    await ctx.send(f"Greeting message set for {channel.mention}.")

@bot.command()
@commands.has_permissions(administrator=True)
async def set_logs(ctx, channel: discord.TextChannel):
    config['logs'][str(ctx.guild.id)] = str(channel.id)
    save_config(config)
    await ctx.send(f"Logs channel set to {channel.mention}.")

@bot.command()
@commands.has_permissions(administrator=True)
async def toggle_automod(ctx):
    config['automod']['enabled'] = not config['automod']['enabled']
    save_config(config)
    status = "enabled" if config['automod']['enabled'] else "disabled"
    await ctx.send(f"Automod {status}.")

@bot.command()
@commands.has_permissions(administrator=True)
async def add_banned_word(ctx, word):
    if word not in config['automod']['banned_words']:
        config['automod']['banned_words'].append(word.lower())
        save_config(config)
        await ctx.send(f"'{word}' added to banned words.")
    else:
        await ctx.send(f"'{word}' is already banned.")

@bot.command()
@commands.has_permissions(administrator=True)
async def toggle_antinuke(ctx):
    config['antinuke']['enabled'] = not config['antinuke']['enabled']
    save_config(config)
    status = "enabled" if config['antinuke']['enabled'] else "disabled"
    await ctx.send(f"Antinuke {status}.")

@bot.command()
@commands.has_permissions(administrator=True)
async def set_prefix(ctx, new_prefix):
    if len(new_prefix) > 5:
        await ctx.send("Prefix must be 5 characters or less.")
        return
    config['prefix'] = new_prefix
    save_config(config)
    await ctx.send(f"Prefix changed to `{new_prefix}`. Please restart the bot for the change to take effect.")

# Panel Command
@bot.slash_command(name="panel", description="Displays the bot's command panel")
async def panel(ctx):
    embed = discord.Embed(
        title="🤖 Bot Command Panel",
        description="Here's a list of all available commands organized by category. Use the slash commands or prefix commands as shown.",
        color=0x00ff00
    )
    
    # Autorole
    embed.add_field(
        name="🔑 Autorole",
        value="`!set_autorole @Role` - Set the role to auto-assign to new members",
        inline=False
    )
    
    # Moderation Commands
    embed.add_field(
        name="🛡️ Moderation",
        value="`!ban @user [reason]` - Ban a member\n"
              "`!kick @user [reason]` - Kick a member\n"
              "`!timeout @user duration [reason]` - Timeout a member (in minutes)\n"
              "`!mute @user [reason]` - Mute a member (requires 'Muted' role)\n"
              "`!unban user_id` - Unban a user by ID\n"
              "`!unmute @user` - Unmute a member\n"
              "`!remove_timeout @user` - Remove timeout from a member",
        inline=False
    )
    
    # Greeting Setup
    embed.add_field(
        name="👋 Greeting Messages",
        value="`!set_greeting #channel message` - Set greeting message for new members\n"
              "*Use {user} in the message to mention the new member*",
        inline=False
    )
    
    # Antinuke and Antiraid
    embed.add_field(
        name="🛡️ Antinuke & Antiraid",
        value="`!toggle_antinuke` - Enable/disable antinuke protection\n"
              "*Automatically detects and prevents mass destructive actions*",
        inline=False
    )
    
    # Automod
    embed.add_field(
        name="🤖 Automod",
        value="`!toggle_automod` - Enable/disable automod\n"
              "`!add_banned_word word` - Add a word to the banned list\n"
              "*Automatically filters banned words and detects spam*",
        inline=False
    )
    
    # Logging System
    embed.add_field(
        name="📝 Logging",
        value="`!set_logs #channel` - Set the channel for logging events\n"
              "*Logs member joins/leaves, role changes, voice updates, and moderation actions*",
        inline=False
    )
    
    embed.set_footer(text="Note: Most commands require administrator or specific permissions. Use /panel to view this again.")
    
    await ctx.respond(embed=embed)

# Run the bot
bot.run('YOUR_BOT_TOKEN_HERE')
