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

    # ------ ここで再読み込み ------
    with open(USER_CONFIG_FILE, 'r', encoding='utf-8') as f:
        new_config = yaml.safe_load(f)
    user_config.clear()
    user_config.update(new_config)

# ------------- 2. Botセットアップ -------------
intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

# ===================================================
# モーダル (検索用)
# ===================================================
class SearchModal(discord.ui.Modal):
    """ユーザーが検索ワードを入力するモーダル"""
    def __init__(self, user_id: str):
        super().__init__(title="メンバー検索")
        self.user_id = user_id

        self.search_input = discord.ui.TextInput(
            label="検索したい文字列を入力",
            placeholder="例) user / abc ...",
            min_length=1,
            max_length=50
        )
        self.add_item(self.search_input)

    async def on_submit(self, interaction: discord.Interaction):
        query = self.search_input.value.lower()
        guild_members = [m for m in interaction.guild.members if not m.bot]
        matched = []

        for m in guild_members:
            # 大文字・小文字を区別しない部分一致
            if query in m.name.lower():
                matched.append(m)

        # SelectMenuは25件までなのでスライス
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
            content=f"検索結果: {len(matched)} 名。該当メンバーを選んで、【確定】または【削除】を押してください。",
            view=view,
            ephemeral=True
        )

# ===================================================
# SelectMenu & ボタン類
# ===================================================
class SelectResultMenu(discord.ui.Select):
    """検索結果を表示するSelectMenu"""
    def __init__(self, user_id: str, members: List[discord.Member]):
        self.user_id = user_id
        options = [
            discord.SelectOption(label=m.name, value=str(m.id))
            for m in members
        ]
        super().__init__(
            placeholder="追加or削除したいメンバーを選択 (複数可)",
            min_values=1,
            max_values=len(options),
            options=options
        )
        self.selected_members = []

    async def callback(self, interaction: discord.Interaction):
        self.selected_members = self.values
        await interaction.response.defer()


class ConfirmButton(discord.ui.Button):
    """選択したメンバーを通知リストに【追加】登録するボタン"""
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
    """選択したメンバーを通知リストから【削除】するボタン"""
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
            updated = existing - selected  # 差集合 = 削除

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
    """SelectMenu + 確定ボタン(追加) + 削除ボタン"""
    def __init__(self, user_id: str, members: List[discord.Member]):
        super().__init__(timeout=None)
        self.user_id = user_id
        self.select_menu = SelectResultMenu(user_id, members)
        self.add_item(self.select_menu)
        self.add_item(ConfirmButton(user_id))
        self.add_item(RemoveButton(user_id))


class SearchButton(discord.ui.Button):
    """「検索」ボタン。押すと検索用モーダルを出す"""
    def __init__(self, user_id: str):
        super().__init__(label="検索", style=discord.ButtonStyle.primary)
        self.user_id = user_id

    async def callback(self, interaction: discord.Interaction):
        modal = SearchModal(self.user_id)
        await interaction.response.send_modal(modal)


class SearchView(discord.ui.View):
    """最初の画面で出す「検索」ボタンのみのView"""
    def __init__(self, user_id: str):
        super().__init__(timeout=None)
        self.add_item(SearchButton(user_id))

# ===============================================
# スラッシュコマンド (/search) : エントリポイント
# ===============================================
@bot.tree.command(name="search", description="検索してメンバーを追加または削除する")
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

    view = SearchView(user_id)
    await interaction.response.send_message(
        "「検索」ボタンを押して、通知リストに追加/削除したいメンバーを探してください。",
        view=view,
        ephemeral=True
    )

# ===========================
# VC参加→DM通知 (既存の処理)
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
