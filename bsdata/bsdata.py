# -*- coding: utf-8 -*-

"""
The MIT License (MIT)

Copyright (c) 2017 SML

Permission is hereby granted, free of charge, to any person obtaining a
copy of this software and associated documentation files (the "Software"),
to deal in the Software without restriction, including without limitation
the rights to use, copy, modify, merge, publish, distribute, sublicense,
and/or sell copies of the Software, and to permit persons to whom the
Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS
OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
DEALINGS IN THE SOFTWARE.
"""

import os
import asyncio
import itertools
import datetime as dt
from random import choice
import json
from urllib.parse import urlunparse
from datetime import timedelta
from enum import Enum

import discord
from discord.ext import commands
from discord.ext.commands import Context

from __main__ import send_cmd_help
from cogs.utils import checks
from cogs.utils.chat_formatting import pagify
from cogs.utils.dataIO import dataIO

try:
    import aiohttp
except ImportError:
    raise ImportError("Please install the aiohttp package.") from None

PATH = os.path.join("data", "bsdata")
JSON = os.path.join(PATH, "settings.json")

DATA_UPDATE_INTERVAL = timedelta(minutes=5).seconds

RESULTS_MAX = 3
PAGINATION_TIMEOUT = 20

SETTINGS_DEFAULTS = {
    "band_api_url": {},
    "player_api_url": {},
    "servers": {}
}
SERVER_DEFAULTS = {
    "bands": {}
}
BAND_DEFAULTS = {
    "name": None,
    "role": "",
    "tag": "",
    "members": []
}
# this one here is mostly for reference
MEMBER_DEFAULTS = {
    "experience_level": 0,
    "id": {
        "high": 0,
        "low": 0,
        "unsigned": False
    },
    "name": "Name",
    "role": "Member",
    "role_id": 1,
    "tag": "XXX",
    "trophies": 0,
    "unk1": 0
}


def grouper(n, iterable, fillvalue=None):
    """Group lists into lists of items.

    grouper(3, 'ABCDEFG', 'x') --> ABC DEF Gxx"""
    args = [iter(iterable)] * n
    return itertools.zip_longest(*args, fillvalue=fillvalue)


class BSRole(Enum):
    """Brawl Stars role."""

    MEMBER = 1
    LEADER = 2
    ELDER = 3
    COLEADER = 4


BS_ROLES = {
    BSRole.MEMBER: "Member",
    BSRole.LEADER: "Leader",
    BSRole.ELDER: "Elder",
    BSRole.COLEADER: "Co-Leader"
}


class BSBandData:
    """Brawl Stars Band data."""

    def __init__(self, **kwargs):
        """Init.

        Expected list of keywords:
        From API:
            badge
            badge_id
            description
            id
            member_count
            members
            name
            required_score
            score
            type
            type_id
            unk1
            unk2
        From cog:
            key
            role
            tag
        """
        self.__dict__.update(kwargs)

    @property
    def member_count_str(self):
        """Member count in #/50 format."""
        count = self.member_count
        if count is None:
            count = 0
        return '{}/50'.format(count)

    @property
    def badge_url(self):
        """Return band emblem URL."""
        return (
            "https://raw.githubusercontent.com"
            "/smlbiobot/smlbiobot.github.io/master/img"
            "/bs-badge/{}.png").format(self.badge)


class BSBandMemberData:
    """Brawl Stars Member data."""

    def __init__(self, **kwargs):
        """Init.

        Expected list of keywords:
        From API:
            experience_level
            id
                high
                low
                unsigned
            name
            role
            role_id
            tag
            trophies
            unk1
            unk2
        """
        self.__dict__.update(kwargs)


class BSBrawlerData:
    """Brawl Stars Brawler data."""

    def __init__(self, **kwargs):
        """Init.

        Expected list of keywords:
        From API:
            highest_trophies
            level
            name
            number
            trophies
            type
            value
        """
        self.__dict__.update(kwargs)

class BSPlayerData:
    """Brawl Stars player data."""

    def __init__(self, **kwargs):
        """Init.

        Expected list of keywords:
        From API:
            avatar
            band
                badge
                badge_id
                high
                low
                member_count
                name
                requirement
                role
                role_id
                tag
                trophies
                type
                type_id
            brawler_count
            brawlers[]
                highest_trophies
                level
                name
                number
                trophies
                type
                value
            high
            highest_trophies
            low
            survival_wins
            tag
            total_experience
            trophies
            username
            wins
        """
        self.__dict__.update(kwargs)

class BSData:
    """Brawl Stars Clan management."""

    def __init__(self, bot):
        """Init."""
        self.bot = bot
        self.task = bot.loop.create_task(self.loop_task())
        self.settings = dataIO.load_json(JSON)

    def __unload(self):
        self.task.cancel()

    async def loop_task(self):
        """Loop task: update data daily."""
        await self.bot.wait_until_ready()
        await self.update_data()
        await asyncio.sleep(DATA_UPDATE_INTERVAL)
        if self is self.bot.get_cog('BSBand'):
            self.task = self.bot.loop.create_task(self.loop_task())

    def check_server_settings(self, server_id):
        """Add server to settings if one does not exist."""
        if server_id not in self.settings["servers"]:
            self.settings["servers"][server_id] = SERVER_DEFAULTS
        dataIO.save_json(JSON, self.settings)

    @commands.group(pass_context=True, no_pm=True)
    @checks.admin_or_permissions(manage_server=True)
    async def setbsdata(self, ctx):
        """Set Brawl Stars Data settings.

        Require: Brawl Stars API by Harmiox.
        May not work for you."""
        if ctx.invoked_subcommand is None:
            await send_cmd_help(ctx)

    @setbsdata.command(name="init", pass_context=True)
    async def setbsdata_init(self, ctx: Context):
        """Init BS Band settings."""
        server = ctx.message.server
        self.settings["servers"][server.id] = SERVER_DEFAULTS
        dataIO.save_json(JSON, self.settings)
        await self.bot.say("Server settings initialized.")

    @setbsdata.command(name="bandapi", pass_context=True)
    async def setbsdata_bandapi(self, ctx: Context, url):
        """BS Band API URL base.

        Format:
        If path is hhttp://domain.com/path/LQQ
        Enter http://domain.com/path/
        """
        self.settings["band_api_url"] = url

        server = ctx.message.server
        self.check_server_settings(server.id)

        dataIO.save_json(JSON, self.settings)
        await self.bot.say("Band API URL updated.")

    @setbsdata.command(name="playerapi", pass_context=True)
    async def setbsdata_playerapi(self, ctx: Context, url):
        """BS Member API URL base.

        Format:
        If path is hhttp://domain.com/path/LQQ
        Enter http://domain.com/path/
        """
        self.settings["player_api_url"] = url

        server = ctx.message.server
        self.check_server_settings(server.id)

        dataIO.save_json(JSON, self.settings)
        await self.bot.say("Member API URL updated.")

    async def get_band_data(self, tag):
        """Return band data JSON."""
        url = "{}{}".format(self.settings["band_api_url"], tag)
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                try:
                    data = await resp.json()
                except json.decoder.JSONDecodeError:
                    data = None
        return data

    async def update_data(self, band_tag=None):
        """Perform data update from api."""
        for server_id in self.settings["servers"]:
            bands = self.get_bands_settings(server_id)

            for tag, band in bands.items():
                do_update = band_tag is None or band_tag == tag
                if do_update:
                    # async with self.get_band_data(tag) as data:
                    data = await self.get_band_data(tag)
                    bands[tag].update(data)
                    self.set_bands_settings(server_id, bands)
        return True

    @setbsdata.command(name="update", pass_context=True)
    async def setbsdata_update(self, ctx: Context):
        """Update data from api."""
        success = await self.update_data()
        if success:
            await self.bot.say("Data updated")

    def get_bands_settings(self, server_id):
        """Return bands in settings."""
        self.check_server_settings(server_id)
        return self.settings["servers"][server_id]["bands"].copy()

    def set_bands_settings(self, server_id, data):
        """Set bands data in settings."""
        self.settings["servers"][server_id]["bands"] = data
        dataIO.save_json(JSON, self.settings)
        return True

    @setbsdata.command(name="add", pass_context=True)
    async def setbsdata_add(self, ctx: Context, *clantags):
        """Add clan tag(s).

        [p]setbsband add LQQ 82RQLR 98VLYJ Q0YG8V

        """
        if not clantags:
            await send_cmd_help(ctx)
            return

        server = ctx.message.server
        self.check_server_settings(server.id)

        for clantag in clantags:
            if clantag.startswith('#'):
                clantag = clantag[1:]

            bands = self.get_bands_settings(server.id)
            if clantag not in bands:
                bands[clantag] = BAND_DEFAULTS

            self.set_bands_settings(server.id, bands)

            await self.bot.say("added Band with clan tag: #{}".format(clantag))

        # await self.update_data()

    @setbsdata.command(name="remove", pass_context=True)
    async def setbsdata_remove(self, ctx: Context, *clantags):
        """Remove clan tag(s).

        [p]setbsband remove LQQ 82RQLR 98VLYJ Q0YG8V

        """
        if not clantags:
            await send_cmd_help(ctx)
            return

        server = ctx.message.server
        bands = self.get_bands_settings(server.id)

        for clantag in clantags:
            if clantag.startswith('#'):
                clantag = clantag[1:]

            removed = bands.pop(clantag, None)
            if removed is None:
                await self.bot.say("{} not in clan settings.".format(clantag))
                return

            self.set_bands_settings(server.id, bands)
            await self.bot.say("Removed #{} from bands.".format(clantag))

    @setbsdata.command(name="setkey", pass_context=True)
    async def setbsdata_setkey(self, ctx, tag, key):
        """Associate band tag with human readable key.

        This is used for running other commands to make
        fetching data easier without having to use
        band tag every time.
        """
        server = ctx.message.server
        bands = self.get_bands_settings(server.id)

        if tag not in bands:
            await self.bot.say(
                "{} is not a band tag you have added".format(tag))
            return
        bands[tag]["key"] = key
        self.set_bands_settings(server.id, bands)
        await self.bot.say("Added {} for band #{}.".format(key, tag))

    @commands.group(pass_context=True, no_pm=True)
    async def bsdata(self, ctx: Context):
        """Brawl Stars band."""
        if ctx.invoked_subcommand is None:
            await send_cmd_help(ctx)

    @bsdata.command(name="info", pass_context=True, no_pm=True)
    async def bsdata_info(self, ctx: Context):
        """Information."""
        server = ctx.message.server
        bands = self.get_bands_settings(server.id)
        for k, band in bands.items():
            # update band data if it was never fetched
            if band["name"] is None:
                await self.update_data(band["tag"])
        embeds = [self.embed_bsdata_info(band) for tag, band in bands.items()]
        embeds = sorted(embeds, key=lambda x: x.title)

        color = ''.join([choice('0123456789ABCDEF') for x in range(6)])
        color = int(color, 16)
        for em in embeds:
            em.color = discord.Color(value=color)
            await self.bot.say(embed=em)

    def embed_bsdata_info(self, band):
        """Return band info embed."""
        data = BSBandData(**band)
        em = discord.Embed(
            title=data.name,
            description=data.description)
        em.add_field(name="Band Trophies", value=data.score)
        em.add_field(name="Type", value=data.type)
        em.add_field(name="Required Trophies", value=data.required_score)
        em.add_field(name="Band Tag", value=data.tag)
        em.add_field(
            name="Members", value=data.member_count_str)
        em.set_thumbnail(url=data.badge_url)
        # em.set_author(name=data.name)
        return em

    @bsdata.command(name="roster", pass_context=True, no_pm=True)
    async def bsdata_roster(self, ctx: Context, key):
        """Return band roster by key.

        Key of each band is set from [p]bsband addkey
        """
        server = ctx.message.server
        bands = self.get_bands_settings(server.id)
        band_result = None
        for k, band in bands.items():
            data = BSBandData(**band)
            if data.key == key:
                band_result = data
                break

        if band_result is None:
            await self.bot.say("Cannot find key {} in settings.".format(key))
            return

        members = band_result.members
        # split results as list of 24
        # because embeds can only contain 25 fields
        # if fields are displayed inline,
        # 24 can fit both 2 and 3-column layout
        members_out = grouper(24, members, None)
        color = self.random_color()
        page = 1
        for members in members_out:
            em = self.embed_bsdata_roster(members)
            em.title = band_result.name
            em.description = (
                "Tag: {} | "
                "Required Trophies: {}").format(
                    band_result.tag, band_result.required_score)
            em.color = discord.Color(value=color)
            em.set_thumbnail(url=band_result.badge_url)
            em.set_footer(
                text="Page {}".format(page),
                icon_url=server.icon_url)
            await self.bot.say(embed=em)
            page = page + 1

    def embed_bsdata_roster(self, members):
        """Return band roster embed."""
        em = discord.Embed(title=".")
        for member in members:
            if member is not None:
                data = BSBandMemberData(**member)
                name = "{}, {}".format(data.name, data.role)
                value = "{}, {} XP, #{}".format(
                    data.trophies,
                    data.experience_level,
                    data.tag)
                em.add_field(name=name, value=value)
        return em

    @bsdata.command(name="player", pass_context=True, no_pm=True)
    async def bsdata_player(self, ctx, tag=None):
        """Return player data by player tag."""
        if tag is None:
            await send_cmd_help(ctx)
            return

        data = await self.get_player_data(tag)
        if data is None:
            await self.bot.say("Error fetching player data.")
            return

        player = BSPlayerData(**data)

        await self.bot.say(embed=self.embed_player(player))

    async def get_player_data(self, tag):
        """Return band data JSON."""
        url = "{}{}".format(self.settings["player_api_url"], tag)
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                try:
                    data = await resp.json()
                except json.decoder.JSONDecodeError:
                    data = None
        return data

    def embed_player(self, player: BSPlayerData):
        """Return band roster embed."""
        em = discord.Embed(
            title=player.username,
            description="#{}".format(player.tag))
        band = BSBandData(**player.band)
        em.color = discord.Color(value=self.random_color())

        em.add_field(name=band.name, value=band.role)
        em.add_field(name="Trophies", value=player.trophies)
        em.add_field(name="Victories", value=player.wins)
        em.add_field(name="Showdown Victories", value=player.survival_wins)
        em.add_field(name="Highest Trophies", value=player.highest_trophies)
        em.add_field(name="Brawlers", value=player.brawler_count)

        for brawler in player.brawlers:
            data = BSBrawlerData(**brawler)
            emoji = self.brawler_emoji(data.name)
            if emoji is None:
                emoji = ''
            em.add_field(
                name='{} {}'.format(data.name, emoji),
                value=data.highest_trophies)

        text = (
            '{0.name}'
            ' Trophies: {0.trophies}'
            ' Requirement: {0.requirement}'
            ' Tag: {0.tag}'
            ' Type: {0.type}').format(band)

        em.set_footer(
            text=text,
            icon_url=band.badge_url)

        return em

    @staticmethod
    def random_color():
        """Return random color as an integer."""
        color = ''.join([choice('0123456789ABCDEF') for x in range(6)])
        color = int(color, 16)
        return color

    def brawler_emoji(self, name):
        """Emoji of the brawler.

        Find emoji against all known servers
        to look for match
        <:emoji_name:emoji_id>
        """
        for server in self.bot.servers:
            for emoji in server.emojis:
                if emoji.name == name:
                    return '<:{}:{}>'.format(emoji.name, emoji.id)
        return None



def check_folder():
    """Check folder."""
    if not os.path.exists(PATH):
        os.makedirs(PATH)


def check_file():
    """Check files."""
    if not dataIO.is_valid_json(JSON):
        dataIO.save_json(JSON, SETTINGS_DEFAULTS)


def setup(bot):
    """Setup bot."""
    check_folder()
    check_file()
    n = BSData(bot)
    bot.add_cog(n)

