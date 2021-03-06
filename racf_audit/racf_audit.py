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

import argparse
import os
import re
from collections import defaultdict, OrderedDict

import discord
import unidecode
import yaml
from cogs.utils import checks
from cogs.utils.chat_formatting import pagify, box, bold
from cogs.utils.dataIO import dataIO
from discord.ext import commands
from tabulate import tabulate
import crapipy
import dateutil.parser
import pprint
import humanize
import datetime as dt
import aiohttp
import json
import asyncio

PATH = os.path.join("data", "racf_audit")
JSON = os.path.join(PATH, "settings.json")


def nested_dict():
    """Recursively nested defaultdict."""
    return defaultdict(nested_dict)


def server_role(server, role_name):
    """Return discord role object by name."""
    return discord.utils.get(server.roles, name=role_name)


def member_has_role(server, member, role_name):
    """Return True if member has specific role."""
    role = discord.utils.get(server.roles, name=role_name)
    return role in member.roles

class RACFAuditException(Exception):
    pass

class CachedClanModels(RACFAuditException):
    pass


class RACFClan:
    """RACF Clan."""

    def __init__(self, name=None, tag=None, role=None, membership_type=None, model=None):
        """Init."""
        self.name = name
        self.tag = tag
        self.role = role
        self.membership_type = membership_type
        self.model = model

    @property
    def repr(self):
        """Representation of the clan. Used for debugging."""
        o = []
        o.append('RACFClan object')
        o.append(
            "{0.name} #{0.tag} | {0.role.name}".format(self)
        )
        members = sorted(self.model.members, key=lambda m: m.name.lower())
        member_names = [m.name for m in members]
        print(member_names)
        o.append(', '.join(member_names))
        return '\n'.join(o)


class DiscordUser:
    """Discord user = player tag association."""

    def __init__(self, user=None, tag=None):
        """Init."""
        self.user = user
        self.tag = tag


class DiscordUsers:
    """List of Discord users."""

    def __init__(self, crclan_cog, server):
        """Init."""
        self.crclan_cog = crclan_cog
        self.server = server
        self._user_list = None

    @property
    def user_list(self):
        """Create multiple DiscordUser from a list of tags.

        players format:
        '99688854348369920': '22Q0VGUP'
        discord_member_id: CR player tag
        """
        if self._user_list is None:
            players = self.crclan_cog.manager.get_players(self.server)
            out = []
            for member_id, player_tag in players.items():
                user = self.server.get_member(member_id)
                if user is not None:
                    out.append(DiscordUser(user=user, tag=player_tag))
            self._user_list = out
        return self._user_list

    def tag_to_member(self, tag):
        """Return Discord member from tag."""
        for u in self.user_list:
            if u.tag == tag:
                return u.user
        return None

    def tag_to_member_id(self, tag):
        """Return Discord member from tag."""
        for u in self.user_list:
            if u.tag == tag:
                return u.user
        return None


class MemberAudit:
    """Member audit object associates API model with discord model."""

    def __init__(self, member_model, server, clans):
        self.member_model = member_model
        self.server = server
        self.clans = clans

    @property
    def discord_member(self):
        return self.member_model.discord_member

    @property
    def has_discord(self):
        return self.discord_member is not None

    @property
    def api_clan_name(self):
        return self.member_model.clan_name

    @property
    def api_is_member(self):
        return self.member_model.role_is_member

    @property
    def api_is_elder(self):
        return self.member_model.role_is_elder

    @property
    def api_is_coleader(self):
        return self.member_model.role_is_coleader

    @property
    def api_is_leader(self):
        return self.member_model.role_is_leader

    @property
    def discord_role_member(self):
        return member_has_role(self.server, self.discord_member, "Member")

    @property
    def discord_role_elder(self):
        return member_has_role(self.server, self.discord_member, "Elder")

    @property
    def discord_role_coleader(self):
        return member_has_role(self.server, self.discord_member, "Co-Leader")

    @property
    def discord_role_leader(self):
        return member_has_role(self.server, self.discord_member, "Leader")

    @property
    def discord_clan_roles(self):
        if self.discord_member is None:
            return []
        return [c.role for c in self.clans if c.role in self.discord_member.roles]


class RACFAudit:
    """RACF Audit.
    
    Requires use of additional cogs for functionality:
    SML-Cogs: cr_api : ClashRoyaleAPI
    SML-Cogs: crclan : CRClan
    SML-Cogs: mm : MemberManagement
    """
    required_cogs = ['cr_api', 'crclan', 'mm']

    def __init__(self, bot):
        """Init."""
        self.bot = bot
        self.settings = dataIO.load_json(JSON)

        with open('data/racf_audit/family_config.yaml') as f:
            self.config = yaml.load(f)

    def cache_file_path(self, clan_tag):
        """Return cache path by clan tag."""
        return os.path.join(PATH, "clans", clan_tag + ".json")

    def save_to_cache(self, clan_models):
        """Save clan models to cache."""
        for clan_model in clan_models:
            dataIO.save_json(self.cache_file_path(clan_model.tag), clan_model.to_dict())

    def load_from_cache(self, clan_tags):
        """Return clan models from cache."""
        clan_models = []
        fp_timestamp = None
        for clan_tag in clan_tags:
            fp = self.cache_file_path(clan_tag)
            if fp_timestamp is None:
                fp_timestamp = os.path.getmtime(fp)
            clan_model = crapipy.models.Clan(dataIO.load_json(fp))
            clan_models.append(clan_model)
        return clan_models

    @property
    def api(self):
        """CR API cog."""
        return self.bot.get_cog("ClashRoyaleAPI")

    @property
    def crclan(self):
        """CRClan cog."""
        return self.bot.get_cog("CRClan")

    def family_clan_models_from_cache(self, server):
        """All family clan models from cache."""
        clans = self.clans(server)
        clan_tags = [c.tag for c in clans]
        clan_models = self.load_from_cache(clan_tags)
        return clan_models

    async def family_clan_models(self, server):
        """All family clan models."""
        clans = self.clans(server)
        clan_tags = [c.tag for c in clans]
        clan_models = []

        try:
            client = crapipy.AsyncClient(token=self.auth)
            clan_models = await client.get_clans(clan_tags)

            self.save_to_cache(clan_models)
            self.settings["cache_timestamp"] = dt.datetime.utcnow().isoformat()
            dataIO.save_json(JSON, self.settings)
            # TODO purely for testing
            # raise crapipy.APIError
        except crapipy.APIError:
            raise crapipy.APIError

        return clan_models

    async def family_member_models(self, server):
        """All family member models."""
        is_cache = False
        clan_models = []
        try:
            clan_models = await self.family_clan_models(server)
        except crapipy.APIError:
            # raise crapipy.APIError
            clan_models = self.family_clan_models_from_cache(server)
            is_cache = True
        members = []
        for clan_model in clan_models:
            for member_model in clan_model.members:
                member_model.clan = clan_model
                members.append(member_model)
        return members, is_cache

    def clan_tags(self, membership_type=None):
        """RACF clans."""
        tags = []
        for clan in self.config['clans']:
            if membership_type is None:
                tags.append(clan['tag'])
            elif membership_type == clan['type']:
                tags.append(clan['tag'])
        return tags

    def clans(self, server):
        """List of RACFClan objects based on config."""
        out = []
        for clan in self.config['clans']:
            out.append(
                RACFClan(
                    name=clan['name'],
                    tag=clan['tag'],
                    role=server_role(server, clan['role_name']),
                    membership_type=clan['type']
                )
            )
        return out

    def clan_roles(self, server):
        """Clan roles."""
        return [clan.role for clan in self.clans(server)]

    def clan_name_to_role(self, server, clan_name):
        """Return Discord Role object by clan name."""
        for clan in self.clans(server):
            if clan.name == clan_name:
                return clan.role
        return None

    def check_cogs(self):
        """Check required cogs are loaded."""
        for cog in self.required_cogs:
            if self.bot.get_cog(cog) is None:
                return False
        return True

    @commands.group(aliases=["racfas"], pass_context=True, no_pm=True)
    @checks.mod_or_permissions(manage_roles=True)
    async def racfauditset(self, ctx):
        """RACF Audit Settings."""
        if ctx.invoked_subcommand is None:
            await self.bot.send_cmd_help(ctx)

    async def update_server_settings(self, ctx, key, value):
        """Set server settings."""
        server = ctx.message.server
        self.settings[server.id][key] = value
        dataIO.save_json(JSON, self.settings)
        await self.bot.say("Updated settings.")

    @racfauditset.command(name="leader", pass_context=True, no_pm=True)
    @checks.mod_or_permissions()
    async def racfauditset_leader(self, ctx, role_name):
        """Leader role name."""
        await self.update_server_settings(ctx, "leader", role_name)

    @racfauditset.command(name="coleader", pass_context=True, no_pm=True)
    @checks.mod_or_permissions()
    async def racfauditset_coleader(self, ctx, role_name):
        """Co-Leader role name."""
        await self.update_server_settings(ctx, "coleader", role_name)

    @racfauditset.command(name="elder", pass_context=True, no_pm=True)
    @checks.mod_or_permissions()
    async def racfauditset_elder(self, ctx, role_name):
        """Elder role name."""
        await self.update_server_settings(ctx, "elder", role_name)

    @racfauditset.command(name="member", pass_context=True, no_pm=True)
    @checks.mod_or_permissions()
    async def racfauditset_member(self, ctx, role_name):
        """Member role name."""
        await self.update_server_settings(ctx, "member", role_name)

    @racfauditset.command(name="auth", pass_context=True, no_pm=True)
    @checks.is_owner()
    async def racfauditset_auth(self, ctx, token):
        """Set API Authentication token."""
        self.settings["auth"] = token
        dataIO.save_json(JSON, self.settings)
        await self.bot.say("Updated settings.")

    @racfauditset.command(name="settings", pass_context=True, no_pm=True)
    @checks.is_owner()
    async def racfauditset_settings(self, ctx):
        """Set API Authentication token."""
        await self.bot.say(box(self.settings))


    @property
    def auth(self):
        """API authentication token."""
        return self.settings.get("auth")

    @commands.group(aliases=["racfa"], pass_context=True, no_pm=True)
    async def racfaudit(self, ctx):
        """RACF Audit."""
        if ctx.invoked_subcommand is None:
            await self.bot.send_cmd_help(ctx)

    @racfaudit.command(name="config", pass_context=True, no_pm=True)
    @checks.mod_or_permissions()
    async def racfaudit_config(self, ctx):
        """Show config."""
        for page in pagify(box(tabulate(self.config['clans'], headers="keys"))):
            await self.bot.say(page)

    def search_args_parser(self):
        """Search arguments parser."""
        # Process arguments
        parser = argparse.ArgumentParser(prog='[p]racfaudit search')

        parser.add_argument(
            'name',
            nargs='?',
            default='_',
            help='IGN')
        parser.add_argument(
            '-c', '--clan',
            nargs='?',
            help='Clan')
        parser.add_argument(
            '-n', '--min',
            nargs='?',
            type=int,
            default=0,
            help='Min Trophies')
        parser.add_argument(
            '-m', '--max',
            nargs='?',
            type=int,
            default=10000,
            help='Max Trophies')
        parser.add_argument(
            '-l', '--link',
            action='store_true',
            default=False
        )

        return parser

    @racfaudit.command(name="search", pass_context=True, no_pm=True)
    @checks.mod_or_permissions(manage_roles=True)
    async def racfaudit_search(self, ctx, *args):
        """Search for member.

        usage: [p]racfaudit search [-h] [-t TAG] name

        positional arguments:
          name                  IGN

        optional arguments:
          -h, --help            show this help message and exit
          -c CLAN, --clan CLAN  Clan name
          -n MIN --min MIN      Min Trophies
          -m MAX --max MAX      Max Trophies
          -l --link             Display link to cr-api.com
        """
        parser = self.search_args_parser()
        try:
            pargs = parser.parse_args(args)
        except SystemExit:
            await self.bot.send_cmd_help(ctx)
            return

        client = crapipy.AsyncClient(token=self.auth)
        clans = await client.get_clans(self.clan_tags())
        # print(clans)

        server = ctx.message.server
        results = []
        await self.bot.type()
        member_models, is_cache = await self.family_member_models(server)

        if is_cache:
            settings_cache_timestamp = self.settings.get("cache_timestamp")
            if settings_cache_timestamp is None:
                await self.bot.say("Cannot reach API and cannot load from cache. Aborting…")
                return
            now = dt.datetime.utcnow()
            cache_time = dateutil.parser.parse(settings_cache_timestamp)
            await self.bot.say("Cannot load from API. Results are from: {}".format(
                humanize.naturaltime(now - cache_time)
            ))

        if pargs.name != '_':
            for member_model in member_models:
                # simple search
                if pargs.name.lower() in member_model.name.lower():
                    results.append(member_model)
                else:
                    # unidecode search
                    s = unidecode.unidecode(member_model.name)
                    s = ''.join(re.findall(r'\w', s))
                    if pargs.name.lower() in s.lower():
                        results.append(member_model)
        else:
            results = member_models
            print(len(results))

        # filter by clan name
        if pargs.clan:
            results = [m for m in results if pargs.clan.lower() in m.clan_name.lower()]

        # filter by trophies
        results = [m for m in results if pargs.min <= m.trophies <= pargs.max]

        limit = 10
        if len(results) > limit:
            await self.bot.say(
                "Found more than {0} results. Returning top {0} only.".format(limit)
            )
            results = results[:limit]

        if len(results):
            out = []
            for member_model in results:
                out.append("**{0.name}** #{0.tag}, {0.clan.name}, {0.role}, {0.trophies}".format(member_model))
                if pargs.link:
                    out.append('http://cr-api.com/profile/{}'.format(member_model.tag))
            for page in pagify('\n'.join(out)):
                await self.bot.say(page)
        else:
            await self.bot.say("No results found.")

    @racfaudit.command(name="run", pass_context=True, no_pm=True)
    @checks.mod_or_permissions(manage_roles=True)
    async def racfaudit_run(self, ctx, *, options=''):
        """Audit the entire RACF family.
        
        Options:
        --removerole   Remove clan role from people who aren’t in clan
        --addrole      Add clan role to people who are in clan
        --exec         Run both add and remove role options
        --debug        Show debug in console 
        """
        server = ctx.message.server
        family_tags = self.crclan.manager.get_bands(server).keys()

        option_exec = '--exec' in options
        option_debug = '--debug' in options

        await self.bot.type()

        clans = self.clans(server)

        # Show settings
        await ctx.invoke(self.racfaudit_config)

        # Create list of all discord users with associated tags
        discord_users = DiscordUsers(crclan_cog=self.crclan, server=server)

        # Member models from API
        member_models = await self.family_member_models(server)

        # associate Discord user to member
        for member_model in member_models:
            member_model.discord_member = discord_users.tag_to_member(member_model.tag)

        if option_debug:
            for du in discord_users.user_list:
                print(du.tag, du.user)

        """
        Member processing.
        
        """
        clan_defaults = {
            "elder_promotion_req": [],
            "coleader_promotion_req": [],
            "no_discord": [],
            "no_clan_role": []
        }
        clans_out = OrderedDict([(c.name, clan_defaults) for c in clans])

        def update_clan(clan_name, field, member_model):
            clans_out[clan_name][field].append(member_model)

        out = []
        for i, member_model in enumerate(member_models):
            if i % 20 == 0:
                await self.bot.type()

            ma = MemberAudit(member_model, server, clans)
            clan_name = member_model.clan_name
            m_out = []
            if ma.has_discord:
                if not ma.api_is_elder and ma.discord_role_elder:
                    update_clan(clan_name, "elder_promotion_req", member_model)
                    m_out.append(":warning: Has Elder role but not promoted in clan.")
                if not ma.api_is_coleader and ma.discord_role_coleader:
                    update_clan(clan_name, "coleader_promotion_req", member_model)
                    m_out.append(":warning: Has Co-Leader role but not promoted in clan.")
                clan_role = self.clan_name_to_role(server, member_model.clan_name)
                if clan_role is not None:
                    if clan_role not in ma.discord_clan_roles:
                        update_clan(clan_name, "no_clan_role", member_model)
                        m_out.append(":warning: Does not have {}".format(clan_role.name))
            else:
                update_clan(clan_name, "no_discord", member_model)
                m_out.append(':x: No Discord')

            if len(m_out):
                out.append(
                    "**{ign}** {clan}\n{status}".format(
                        ign=member_model.name,
                        clan=member_model.clan_name,
                        status='\n'.join(m_out)
                    )
                )

        # line based output
        for page in pagify('\n'.join(out)):
            await self.bot.type()
            await self.bot.say(page)

        # clan based output
        out = []
        print(clans_out)
        for clan_name, clan_dict in clans_out.items():
            out.append("**{}**".format(clan_name))
            if len(clan_dict["elder_promotion_req"]):
                out.append("Elders that need to be promoted:")
                out.append(", ".join([m.name for m in clan_dict["elder_promotion_req"]]))
            if len(clan_dict["no_discord"]):
                out.append("No Discord:")
                out.append(", ".join([m.name for m in clan_dict["no_discord"]]))
            if len(clan_dict["no_clan_role"]):
                out.append("No clan role on Discord:")
                out.append(", ".join([m.name for m in clan_dict["no_clan_role"]]))

        for page in pagify('\n'.join(out), shorten_by=24):
            await self.bot.type()
            if len(page):
                await self.bot.say(page)


def check_folder():
    """Check folder."""
    os.makedirs(PATH, exist_ok=True)
    os.makedirs(os.path.join(PATH, "clans"), exist_ok=True)


def check_file():
    """Check files."""
    if not dataIO.is_valid_json(JSON):
        dataIO.save_json(JSON, {})


def setup(bot):
    """Setup."""
    check_folder()
    check_file()
    n = RACFAudit(bot)
    bot.add_cog(n)
