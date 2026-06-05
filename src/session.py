from __future__ import annotations

from dataclasses import dataclass, field

import discord


"""
セッション管理モジュール。

`PomoSession` は 1 回のポモドーロ実行に関する状態を保持します。
`SessionManager` は複数の `PomoSession` を管理し、ユーザーからセッションを検索・作成・削除する
ためのユーティリティを提供します。
"""


@dataclass
class PomoSession:
    """1つのポモドーロセッションの状態を保持するデータクラス。

    フィールドの意味（主なもの）:
    - `host_id`: セッションのホスト（作成者）のユーザーID
    - `targets`: 参加メンバーのID集合
    - `join_order`: 参加順を保持するリスト（ホストは最初に追加されます）
    - `work_min`, `short_brk`, `long_brk`, `interval`: タイマー設定
    - `session_count`, `session_work`: 実行中の集計情報
    - `pomo_view`, `pomo_msg`, `control_msg`, `join_view`, `join_msg`: Discord UI 関連オブジェクト
    """
    # ユーザー情報
    host_id: int
    targets: set[int] = field(default_factory=set)
    join_order: list[int] = field(default_factory=list)
    # セッション設定
    work_min: int = 25
    short_brk: int = 5
    long_brk: int = 15
    interval: int = 4
    timer_name: str = "original"
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
        # ホストと参加者の全ユーザーID集合を返す
        return {self.host_id} | set(self.targets)

    def get_vc_active_ids(self, voice_client: discord.VoiceClient | None) -> list[int]:
        # ボイスチャンネル上で現在アクティブな対象ユーザーIDを返す
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
        # ボイスチャット内に対象ユーザーが1人以上いるかを判定する
        return len(self.get_vc_active_ids(voice_client)) > 0

    def transfer_host(self, active_ids: set[int] | None = None) -> int | None:
        # ホストを参加者の中から順に移譲する（移譲先IDを返す）
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
        # 対象メンバーに追加する。既にいる場合は False を返す
        if user_id == self.host_id or user_id in self.targets:
            return False
        self.targets.add(user_id)
        if user_id not in self.join_order:
            self.join_order.append(user_id)
        return True

    def remove_member(self, user_id: int) -> bool:
        # 対象メンバーから削除する。削除できたら True
        if user_id in self.targets:
            self.targets.remove(user_id)
            return True
        return False

    def get_target_line(self) -> str:
        # 対象ユーザーのメンション行を作成して文字列で返す
        mentions = [f"<@{self.host_id}>"]
        ordered_targets = [uid for uid in self.join_order if uid in self.targets]
        mentions.extend([f"<@{uid}>" for uid in ordered_targets])
        for uid in sorted(self.targets):
            if uid not in ordered_targets:
                mentions.append(f"<@{uid}>")
        return " ".join(mentions)

    def reset_members(self) -> None:
        self.targets.clear()
        self.join_order = [self.host_id]



class SessionManager:
    def __init__(self):
        # セッション管理用辞書とユーザ→セッションオーナーのインデックス
        self._sessions: dict[int, PomoSession] = {}
        self._user_index: dict[int, int] = {}

    def create(self, author_id: int, **kwargs) -> PomoSession:
        # 新しい PomoSession を作成して管理下に追加する
        session = PomoSession(host_id=author_id, **kwargs)
        session.join_order.append(author_id)
        self._sessions[author_id] = session
        self.update_index(author_id)
        return session

    def get(self, author_id: int) -> PomoSession | None:
        # 指定オーナーのセッションを取得する
        return self._sessions.get(author_id)

    def remove(self, author_id: int) -> None:
        # 所有セッションを削除し、インデックスから関連エントリを消す
        self._sessions.pop(author_id, None)
        stale_users = [uid for uid, owner in self._user_index.items() if owner == author_id]
        for uid in stale_users:
            self._user_index.pop(uid, None)

    def find_by_user(self, user_id: int) -> tuple[int, PomoSession] | None:
        # ユーザーIDから所属セッションのオーナーIDとセッションを返す
        author_id = self._user_index.get(user_id)
        if author_id is None:
            return None
        session = self._sessions.get(author_id)
        if session is None:
            self._user_index.pop(user_id, None)
            return None
        return author_id, session

    def update_index(self, author_id: int) -> None:
        # 指定オーナーのセッションに基づきユーザ→オーナーのインデックスを更新する
        session = self._sessions.get(author_id)
        stale_users = [uid for uid, owner in self._user_index.items() if owner == author_id]
        for uid in stale_users:
            self._user_index.pop(uid, None)
        if session is None:
            return
        indexed_ids = session.get_all_member_ids() if session.active else {session.host_id}
        for uid in indexed_ids:
            self._user_index[uid] = author_id
