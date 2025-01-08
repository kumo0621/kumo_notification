import discord
from discord.ext import commands
import yaml
import os
from typing import List

# ------------- 1. 設定読み込み -------------
with open('config.yml', 'r', encoding='utf-8') as f:
    config = yaml.safe_load(f)

TOKEN = config['token']
COMMAND_CHANNEL_ID = config['command_channel_id']  # コマンド実行チャンネル
VOICE_CHANNEL_IDS = config['voice_channel_ids']    # 通知対象VCのID

USER_CONFIG_FILE = 'user_config.yml'
if not os.path.exists(USER_CONFIG_FILE):
    with open(USER_CONFIG_FILE, 'w', encoding='utf-8') as f:
        yaml.dump({'users': {}}, f)

with open(USER_CONFIG_FILE, 'r', encoding='utf-8') as f:
    user_config = yaml.safe_load(f)

def save_user_config():
    with open(USER_CONFIG_FILE, 'w', encoding='utf-8') as f:
        yaml.dump(user_config, f, allow_unicode=True)

intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)


# ===================================================
# 2. 「検索」モーダル (名前 or ID で部分一致)
# ===================================================
class SearchModal(discord.ui.Modal):
    """ユーザーが検索ワードを入力するモーダル"""
    def __init__(self, user_id: str):
        super().__init__(title="メンバー検索")
        self.user_id = user_id

        self.search_input = discord.ui.TextInput(
            label="検索したい文字列 (名前 / ニックネーム / ユーザーID)",
            placeholder="例) user / abc / 12345 ...",
            min_length=1,
            max_length=50
        )
        self.add_item(self.search_input)

    async def on_submit(self, interaction: discord.Interaction):
        query = self.search_input.value.lower()
        guild_members = [m for m in interaction.guild.members if not m.bot]
        matched = []

        for m in guild_members:
            # ニックネームがあれば小文字化して検索対象にする
            nickname_lower = m.nick.lower() if m.nick else ""

            # 「表示名」 or 「ニックネーム」 or 「ID」いずれかにqueryが部分一致
            if (query in m.name.lower()) or (query in nickname_lower) or (query in str(m.id)):
                matched.append(m)

        # DiscordのSelectMenuは25件まで
        matched = matched[:25]

        if not matched:
            await interaction.response.send_message(
                f"「{query}」に一致するメンバーが見つかりませんでした。",
                ephemeral=True
            )
            return

        # 一致したメンバーを表示するSelectMenu
        view = SelectResultView(self.user_id, matched)
        await interaction.response.send_message(
            content=(
                f"検索結果: {len(matched)} 名。\n"
                "追加したい or 削除したいメンバーを選び、[確定] または [削除] を押してください。"
            ),
            view=view,
            ephemeral=True
        )


# ===================================================
# 3. 「検索結果表示」SelectMenu + 追加/削除ボタン
# ===================================================
class SelectResultMenu(discord.ui.Select):
    """検索結果を表示するSelectMenu"""
    def __init__(self, user_id: str, members: List[discord.Member]):
        self.user_id = user_id
        options = []
        for m in members:
            # ラベルは表示名、値はユーザーID
            options.append(
                discord.SelectOption(label=f"{m.name}", value=str(m.id))
            )
        super().__init__(
            placeholder="操作対象のメンバーを選択 (複数可)",
            min_values=1,
            max_values=len(options),
            options=options
        )
        self.selected_members = []

    async def callback(self, interaction: discord.Interaction):
        # 選んだメンバーのIDが self.values に入る
        self.selected_members = self.values
        await interaction.response.defer()

class ConfirmButton(discord.ui.Button):
    """選択したメンバーを通知リストに追加するボタン"""
    def __init__(self, user_id: str):
        super().__init__(label="確定", style=discord.ButtonStyle.primary)
        self.user_id = user_id

    async def callback(self, interaction: discord.Interaction):
        view: SelectResultView = self.view
        select_menu: SelectResultMenu = view.select_menu

        if self.user_id not in user_config["users"]:
            user_config["users"][self.user_id] = {"selected_members": []}

        existing = set(user_config["users"][self.user_id].get("selected_members", []))
        selected = set(select_menu.selected_members)
        updated = existing | selected  # union = 追加

        user_config["users"][self.user_id]["selected_members"] = list(updated)
        save_user_config()

        await interaction.response.send_message(
            f"{len(selected)}名を通知リストに追加しました。",
            ephemeral=True
        )
        for child in view.children:
            child.disabled = True
        await interaction.edit_original_response(view=view)

class RemoveButton(discord.ui.Button):
    """選択したメンバーを通知リストから削除するボタン"""
    def __init__(self, user_id: str):
        super().__init__(label="削除", style=discord.ButtonStyle.danger)
        self.user_id = user_id

    async def callback(self, interaction: discord.Interaction):
        view: SelectResultView = self.view
        select_menu: SelectResultMenu = view.select_menu

        if self.user_id not in user_config["users"]:
            await interaction.response.send_message("通知リストがありません。", ephemeral=True)
        else:
            existing = set(user_config["users"][self.user_id].get("selected_members", []))
            selected = set(select_menu.selected_members)
            updated = existing - selected

            user_config["users"][self.user_id]["selected_members"] = list(updated)
            save_user_config()

            await interaction.response.send_message(
                f"{len(selected)}名を通知リストから削除しました。",
                ephemeral=True
            )

        for child in view.children:
            child.disabled = True
        await interaction.edit_original_response(view=view)

class SelectResultView(discord.ui.View):
    """SelectMenu + 追加ボタン + 削除ボタン"""
    def __init__(self, user_id: str, members: List[discord.Member]):
        super().__init__(timeout=None)
        self.user_id = user_id
        self.select_menu = SelectResultMenu(user_id, members)
        self.add_item(self.select_menu)
        self.add_item(ConfirmButton(user_id))
        self.add_item(RemoveButton(user_id))


# ===================================================
# 4. 「検索ボタン」「登録一覧表示ボタン」をまとめたView
# ===================================================
class SearchButton(discord.ui.Button):
    """「検索」ボタン"""
    def __init__(self, user_id: str):
        super().__init__(label="検索", style=discord.ButtonStyle.primary)
        self.user_id = user_id

    async def callback(self, interaction: discord.Interaction):
        modal = SearchModal(self.user_id)
        await interaction.response.send_modal(modal)

class ShowRegisteredButton(discord.ui.Button):
    """「登録中メンバーを表示」ボタン"""
    def __init__(self, user_id: str):
        super().__init__(label="登録一覧", style=discord.ButtonStyle.secondary)
        self.user_id = user_id

    async def callback(self, interaction: discord.Interaction):
        # user_config.yml から user_id の selected_members を取得
        if self.user_id not in user_config["users"]:
            await interaction.response.send_message("登録情報がありません。", ephemeral=True)
            return

        member_ids = user_config["users"][self.user_id].get("selected_members", [])
        if not member_ids:
            await interaction.response.send_message("現在、通知リストには誰も登録されていません。", ephemeral=True)
            return

        # IDから guild.get_member(...) で名前を取得し、一覧文字列を作る
        guild = interaction.guild
        lines = []
        for mid in member_ids:
            # guild.get_member(...) は存在しないIDだと None になる場合もある
            member = guild.get_member(int(mid))
            if member:
                lines.append(f"- {member.name} (ID: {member.id})")
            else:
                # サーバーを抜けたメンバーなど
                lines.append(f"- UnknownMember(ID: {mid})")

        msg = "**現在通知登録されているメンバー:**\n" + "\n".join(lines)
        await interaction.response.send_message(msg, ephemeral=True)

class SearchView(discord.ui.View):
    """検索ボタンと「登録一覧」ボタンのView"""
    def __init__(self, user_id: str):
        super().__init__(timeout=None)
        self.add_item(SearchButton(user_id))
        self.add_item(ShowRegisteredButton(user_id))


# ===================================================
# 5. スラッシュコマンド (/search) : エントリポイント
# ===================================================
@bot.tree.command(name="search", description="検索してメンバーを追加・削除、あるいは登録一覧を確認")
async def search_command(interaction: discord.Interaction):
    # コマンド実行チャンネルが特定のIDでなければ弾く
    if interaction.channel.id != COMMAND_CHANNEL_ID:
        await interaction.response.send_message(
            "このコマンドは指定されたチャンネルでのみ使用できます。",
            ephemeral=True
        )
        return

    user_id = str(interaction.user.id)
    if user_id not in user_config["users"]:
        user_config["users"][user_id] = {"selected_members": []}
        save_user_config()

    # 「検索ボタン」「登録一覧ボタン」を持つViewを返す
    view = SearchView(user_id)
    await interaction.response.send_message(
        "メンバーを検索したい場合は[検索]、\n現在の登録中メンバーを確認したい場合は[登録一覧]ボタンを押してください。",
        view=view,
        ephemeral=True
    )

# ===========================
# 6. VC参加→DM通知 (既存の処理)
# ===========================
@bot.event
async def on_voice_state_update(member, before, after):
    if before.channel == after.channel:
        return
    if after.channel is None:
        return
    if after.channel.id not in VOICE_CHANNEL_IDS:
        return

    moved_member_id_str = str(member.id)
    for user_id, data in user_config.get("users", {}).items():
        selected_ids = data.get("selected_members", [])
        if moved_member_id_str in selected_ids:
            try:
                user_to_notify = await bot.fetch_user(int(user_id))
                if user_to_notify:
                    await user_to_notify.send(
                        f"【{member.guild.name}】\n"
                        f"{member.name} さんがVC「{after.channel.name}」に参加しました。"
                    )
            except Exception as e:
                print("DM送信エラー:", e)

@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"Logged in as {bot.user}")

bot.run(TOKEN)
