from __future__ import annotations

from dataclasses import dataclass, field

import discord


@dataclass
class PomoSession:
    # ユーザー情報
    host_id: int
    targets: set[int] = field(default_factory=set)
    join_order: list[int] = field(default_factory=list)
    # セッション設定
    work_min: int = 25
    short_brk: int = 5
    long_brk: int = 15
    interval: int = 4
    # セッション状態
    session_count: int = 0
    session_work: dict[int, int] = field(default_factory=dict)
    muted: bool = False
    active: bool = False
    stop_requested: bool = False
    # UI関連
    pomo_view: "PomoView | None" = field(default=None, repr=False)
    pomo_msg: "discord.Message | None" = field(default=None, repr=False)
    control_msg: "discord.Message | None" = field(default=None, repr=False)
    join_view: "JoinView | None" = field(default=None, repr=False)
    join_msg: "discord.Message | None" = field(default=None, repr=False)

    def get_all_member_ids(self) -> set[int]:
        return {self.host_id} | set(self.targets)

    def get_vc_active_ids(self, voice_client: discord.VoiceClient | None) -> list[int]:
        if not voice_client or not voice_client.channel:
            return []

        voice_states = getattr(voice_client.channel, "voice_states", None)
        if isinstance(voice_states, dict):
            vc_member_ids = set(voice_states.keys())
        else:
            vc_member_ids = set()

        if not vc_member_ids:
            vc_member_ids = {m.id for m in voice_client.channel.members if not m.bot}

        if not vc_member_ids:
            guild = getattr(voice_client, "guild", None)
            host_member = guild.get_member(self.host_id) if guild else None
            if (
                host_member
                and host_member.voice
                and host_member.voice.channel
                and host_member.voice.channel == voice_client.channel
            ):
                vc_member_ids.add(self.host_id)

        active_ids = vc_member_ids & self.get_all_member_ids()
        return list(active_ids)

    def has_active_members(self, voice_client: discord.VoiceClient | None) -> bool:
        return len(self.get_vc_active_ids(voice_client)) > 0

    def transfer_host(self, active_ids: set[int] | None = None) -> int | None:
        for user_id in self.join_order:
            if user_id == self.host_id or user_id not in self.targets:
                continue
            if active_ids is not None and user_id not in active_ids:
                continue
            self.targets.remove(user_id)
            self.host_id = user_id
            return user_id
        return None

    def add_member(self, user_id: int) -> bool:
        if user_id == self.host_id or user_id in self.targets:
            return False
        self.targets.add(user_id)
        if user_id not in self.join_order:
            self.join_order.append(user_id)
        return True

    def remove_member(self, user_id: int) -> bool:
        if user_id in self.targets:
            self.targets.remove(user_id)
            return True
        return False

    def get_target_line(self) -> str:
        mentions = [f"<@{self.host_id}>"]
        ordered_targets = [uid for uid in self.join_order if uid in self.targets]
        mentions.extend([f"<@{uid}>" for uid in ordered_targets])
        for uid in sorted(self.targets):
            if uid not in ordered_targets:
                mentions.append(f"<@{uid}>")
        return " ".join(mentions)


class SessionManager:
    def __init__(self):
        self._sessions: dict[int, PomoSession] = {}
        self._user_index: dict[int, int] = {}

    def create(self, author_id: int, **kwargs) -> PomoSession:
        session = PomoSession(host_id=author_id, **kwargs)
        session.join_order.append(author_id)
        self._sessions[author_id] = session
        self.update_index(author_id)
        return session

    def get(self, author_id: int) -> PomoSession | None:
        return self._sessions.get(author_id)

    def remove(self, author_id: int) -> None:
        self._sessions.pop(author_id, None)
        stale_users = [uid for uid, owner in self._user_index.items() if owner == author_id]
        for uid in stale_users:
            self._user_index.pop(uid, None)

    def find_by_user(self, user_id: int) -> tuple[int, PomoSession] | None:
        author_id = self._user_index.get(user_id)
        if author_id is None:
            return None
        session = self._sessions.get(author_id)
        if session is None:
            self._user_index.pop(user_id, None)
            return None
        return author_id, session

    def update_index(self, author_id: int) -> None:
        session = self._sessions.get(author_id)
        stale_users = [uid for uid, owner in self._user_index.items() if owner == author_id]
        for uid in stale_users:
            self._user_index.pop(uid, None)
        if session is None:
            return
        indexed_ids = session.get_all_member_ids() if session.active else {session.host_id}
        for uid in indexed_ids:
            self._user_index[uid] = author_id
