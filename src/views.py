from __future__ import annotations

import discord
from discord.ui import Button, View

from session import PomoSession, SessionManager


class PomoView(View):
    def __init__(self, session: PomoSession):
        super().__init__(timeout=None)
        self.session = session
        self.paused = False
        self.stopped = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id in self.session.get_all_member_ids()

    @discord.ui.button(label="一時停止", style=discord.ButtonStyle.secondary, emoji="⏸️")
    async def pause_button(self, interaction: discord.Interaction, button: Button):
        self.paused = True
        button.disabled = True
        self.children[1].disabled = False
        await interaction.response.edit_message(content="⏸️ タイマーを一時停止しました。", view=self)

    @discord.ui.button(label="再開", style=discord.ButtonStyle.success, emoji="▶️", disabled=True)
    async def resume_button(self, interaction: discord.Interaction, button: Button):
        self.paused = False
        button.disabled = True
        self.children[0].disabled = False
        await interaction.response.edit_message(content="▶️ タイマーを再開します。", view=self)

    @discord.ui.button(label="終了", style=discord.ButtonStyle.danger, emoji="⏹️")
    async def stop_button(self, interaction: discord.Interaction, button: Button):
        self.stopped = True
        await interaction.response.edit_message(content="⏹️ タイマーを終了しました。", view=None)
        self.stop()


class JoinView(View):
    def __init__(self, session: PomoSession, manager: SessionManager, author_id: int):
        super().__init__(timeout=None)
        self.session = session
        self.manager = manager
        self.author_id = author_id

    @discord.ui.button(label="参加", style=discord.ButtonStyle.success, emoji="🙋")
    async def join_button(self, interaction: discord.Interaction, button: Button):
        user = interaction.user
        if user.bot:
            await interaction.response.send_message("⚠️ Botは参加できません。", ephemeral=True)
            return
        if self.session.add_member(user.id):
            self.manager.update_index(self.author_id)
            await interaction.response.send_message("✅ タイマー対象に参加しました。", ephemeral=True)
        else:
            await interaction.response.send_message("ℹ️ 既に参加済みです。", ephemeral=True)

    @discord.ui.button(label="退出", style=discord.ButtonStyle.secondary, emoji="👋")
    async def leave_button(self, interaction: discord.Interaction, button: Button):
        user = interaction.user

        if user.id == self.session.host_id:
            guild_vc = interaction.guild.voice_client if interaction.guild else None
            active_ids = set(self.session.get_vc_active_ids(guild_vc))
            new_host = self.session.transfer_host(active_ids=active_ids)
            self.manager.update_index(self.author_id)
            if new_host:
                await interaction.response.send_message(
                    f"👋 {user.mention} が退出しました。ホストが <@{new_host}> に移行しました。"
                )
            else:
                self.session.stop_requested = True
                print(f"[DEBUG] stop_requested=True (leave_button) host={user.id}")
                await interaction.response.send_message(
                    f"👋 {user.mention} が退出しました。タイマーを終了します。"
                )
            return

        if self.session.remove_member(user.id):
            self.manager.update_index(self.author_id)
            await interaction.response.send_message(f"👋 {user.mention} が退出しました。", ephemeral=True)
        else:
            await interaction.response.send_message("ℹ️ 参加していません。", ephemeral=True)
