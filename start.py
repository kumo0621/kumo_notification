import discord
from discord.ext import commands
import yaml
import os

# ------------------------------------------------
# 1. 設定ファイル (config.yml) を読み込み
# ------------------------------------------------
with open('config.yml', 'r', encoding='utf-8') as f:
    config = yaml.safe_load(f)

TOKEN = config['token']
COMMAND_CHANNEL_ID = config['command_channel_id']  # コマンドOKなテキストチャンネル
VOICE_CHANNEL_IDS = config['voice_channel_ids']    # 通知対象のVCリスト

# ------------------------------------------------
# 2. user_config.yml の読み込み・初期化
# ------------------------------------------------
USER_CONFIG_FILE = 'user_config.yml'
if not os.path.exists(USER_CONFIG_FILE):
    with open(USER_CONFIG_FILE, 'w', encoding='utf-8') as f:
        yaml.dump({'users': {}}, f)

with open(USER_CONFIG_FILE, 'r', encoding='utf-8') as f:
    user_config = yaml.safe_load(f)

def save_user_config():
    with open(USER_CONFIG_FILE, 'w', encoding='utf-8') as f:
        yaml.dump(user_config, f, allow_unicode=True)

# ------------------------------------------------
# 3. Bot本体とIntents
# ------------------------------------------------
intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)


# ------------------------------------------------
# 4. UIコンポーネント（SelectMenu, 確定ボタン, 削除ボタン）
# ------------------------------------------------
class MemberSelect(discord.ui.Select):
    """
    サーバーメンバーを表示するSelectMenu。
    一旦 self.selected_members に選択されたメンバーIDリストを保持し、
    確定ボタンが押されたときに user_config に保存する。
    """
    def __init__(self, members: list[discord.Member]):
        options = [
            discord.SelectOption(label=m.name, value=str(m.id))
            for m in members
        ]
        super().__init__(
            placeholder="通知したいメンバーを選択(複数可)",
            min_values=1,
            max_values=len(options),
            options=options
        )
        self.selected_members = []

    async def callback(self, interaction: discord.Interaction):
        # 選択されたメンバーのIDを保持
        self.selected_members = self.values
        await interaction.response.defer()  # 特に表示を変えないなら defer() でOK


class ConfirmButton(discord.ui.Button):
    """
    「確定」ボタン。
    押されたとき、SelectMenuに格納されている selected_members を user_config に保存する。
    """
    def __init__(self, user_id: str):
        super().__init__(label="確定", style=discord.ButtonStyle.green)
        self.user_id = user_id

    async def callback(self, interaction: discord.Interaction):
        view: MemberSelectView = self.view
        select: MemberSelect = view.select_menu

        # user_config に反映
        if self.user_id not in user_config['users']:
            user_config['users'][self.user_id] = {'selected_members': []}
        user_config['users'][self.user_id]['selected_members'] = select.selected_members
        save_user_config()

        await interaction.response.send_message(
            f"{len(select.selected_members)}名を登録しました。",
            ephemeral=True
        )

        # ボタンやセレクトを無効化して操作不可に
        for child in view.children:
            child.disabled = True
        await interaction.edit_original_response(view=view)


class DeleteButton(discord.ui.Button):
    """
    「削除」ボタン。
    押されたとき、登録されている selected_members を空にする。
    """
    def __init__(self, user_id: str):
        super().__init__(label="削除", style=discord.ButtonStyle.danger)
        self.user_id = user_id

    async def callback(self, interaction: discord.Interaction):
        # user_config から該当ユーザーの selected_members を削除
        if self.user_id not in user_config['users']:
            await interaction.response.send_message("登録情報がありません。", ephemeral=True)
        else:
            user_config['users'][self.user_id]['selected_members'] = []
            save_user_config()
            await interaction.response.send_message("登録メンバーを削除しました。", ephemeral=True)

        # ボタンやセレクトを無効化して操作不可に
        view: MemberSelectView = self.view
        for child in view.children:
            child.disabled = True
        await interaction.edit_original_response(view=view)


class MemberSelectView(discord.ui.View):
    """
    SelectMenu, 確定ボタン, 削除ボタンをひとまとめにしたView。
    """
    def __init__(self, user_id: str, members: list[discord.Member]):
        super().__init__(timeout=None)
        self.user_id = user_id

        self.select_menu = MemberSelect(members)
        self.add_item(self.select_menu)

        confirm_button = ConfirmButton(user_id)
        self.add_item(confirm_button)

        delete_button = DeleteButton(user_id)
        self.add_item(delete_button)


# ------------------------------------------------
# 5. スラッシュコマンド (/tuuti)
# ------------------------------------------------
@bot.tree.command(name="tuuti", description="通知したいメンバーを登録する")
async def start_command(interaction: discord.Interaction):
    """
    特定のテキストチャンネルでのみ反応。
    メンバー選択UIを同じチャンネル内に表示する。
    """
    # 1) コマンド打ったチャンネルが指定のIDか確認
    if interaction.channel.id != COMMAND_CHANNEL_ID:
        await interaction.response.send_message(
            "このコマンドは特定のテキストチャンネルでのみ使用できます。",
            ephemeral=True
        )
        return

    user_id = str(interaction.user.id)
    # ユーザーのエントリが無ければ初期化
    if user_id not in user_config['users']:
        user_config['users'][user_id] = {'selected_members': []}
        save_user_config()

    # 2) サーバーの全メンバー（Botは除外 etc.）を取得
    guild_members = [m for m in interaction.guild.members if not m.bot]

    # 3) Viewを作って返す (同じチャンネルにUIが表示される)
    view = MemberSelectView(user_id, guild_members)
    await interaction.response.send_message(
        content="通知したいメンバーを選択し、確定または削除を押してください。",
        view=view,
        ephemeral=True  # 他のユーザーに見えなくて良い場合はTrueにする
    )


# ------------------------------------------------
# 6. VC参加を監視し、DMで通知
# ------------------------------------------------
@bot.event
async def on_voice_state_update(member, before, after):
    """
    誰かがVCに移動したら、通知対象に登録されているかチェックし、
    該当するユーザー(複数の可能性あり)にDMを飛ばす。
    """
    if before.channel == after.channel:
        return
    if after.channel is None:
        return

    if after.channel.id not in VOICE_CHANNEL_IDS:
        return

    moved_member_id_str = str(member.id)

    for user_id, data in user_config.get('users', {}).items():
        selected_ids = data.get('selected_members', [])
        if moved_member_id_str in selected_ids:
            try:
                user_to_notify = await bot.fetch_user(int(user_id))
                if user_to_notify is not None:
                    await user_to_notify.send(
                        f"**{member.guild.name}** で、**{member.name}** さんが VC **{after.channel.name}** に参加しました！"
                    )
            except Exception as e:
                print("DM送信エラー:", e)


# ------------------------------------------------
# 7. Bot起動 & コマンド同期
# ------------------------------------------------
@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"Logged in as {bot.user}")

bot.run(TOKEN)
