# Telegram Giveaway Bot / 电报抽奖机器人

[English](#english) | [中文](#中文)

---

## English

A highly customizable and lightweight Telegram giveaway bot built with **Python 3.11+**, **aiogram v3**, and **SQLAlchemy**. It supports persistent scheduling, channel-membership restrictions, multilingual localization, and optional Cloudflare Turnstile anti-bot verification.

### Features
* **Bilingual Support**: User-switchable languages (English and Simplified Chinese).
* **Multiple Modes**: Support for public HTTPS Webhook or local/simple Long Polling mode.
* **Persistent Scheduling**: Built-in SQLite database with `APScheduler` restores pending draws on restart, so a redeploy cannot lose a giveaway.
* **Required Channels**: Entry is refused unless the user is actually a member of every required channel (checked live at join time, fail-closed). The bot must be an administrator in each required channel/group to reliably inspect other members.
* **Anti-Bot Verification**: Optional Cloudflare Turnstile verification. A passed challenge is stored as a single-use proof bound to one user and one giveaway; the wizard refuses to enable the captcha unless Turnstile is actually configured.
* **Admin Tools**: In-chat wizard (`/newgiveaway`) plus `/list`, `/cancel`, `/reroll`, `/retry`, `/ban`, `/unban`, `/addadmin`, `/admins`.
* **Auditable Draws**: Winners are drawn with `secrets.SystemRandom()`, a user can win at most one tier, rerolls only apply to finished giveaways and keep the superseded draw on record, and every winner DM is logged as delivered or failed.
* **Health Endpoint**: `GET /healthz` is served by the built-in aiohttp server in both modes.

### Directory Structure
```
tg-giveaway-bot/
├── bot/                  # Source code directory
│   ├── db/               # SQLite Models & Database repository
│   ├── handlers/         # Telegram FSM and callback handlers
│   ├── services/         # Business logic & scheduler tasks
│   ├── web/              # Aiohttp server for webhook & status
│   └── settings.py       # Configuration loading (Pydantic-Settings)
├── locales/              # Translation files (YAML)
├── templates/            # Jinja2 text templates for giveaway posts
├── pyproject.toml        # Project dependencies and tool configurations
└── requirements.txt      # Required Python packages
```

### Installation
1. **Clone the repository**:
   ```bash
   git clone https://github.com/yourusername/tg-giveaway-bot.git
   cd tg-giveaway-bot
   ```

2. **Set up a Virtual Environment**:
   ```bash
   python -m venv .venv
   # Windows:
   .venv\Scripts\activate
   # Linux/macOS:
   source .venv/bin/activate
   ```

3. **Install Dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

4. **Configuration**:
   Copy `.env.example` to `.env` and fill in the required variables:
   ```bash
   cp .env.example .env
   ```
   Key environment variables:
   * `BOT_TOKEN`: The token provided by [@BotFather](https://t.me/BotFather).
   * `ADMIN_IDS`: Comma-separated Telegram User IDs of administrators, or a JSON array such as `[123456,789012]`.
   * `WEBHOOK_BASE_URL`: (Optional) Public URL (e.g. `https://giveaway.example.com`, no trailing slash) for webhook mode. Leave blank to run the bot in **Long Polling** mode.
   * `WEB_HOST` / `WEB_PORT`: Server bind configuration (Defaults to `127.0.0.1:8080`).
   * `WEBHOOK_SECRET`: Required **only in webhook mode** (startup fails fast if it is missing there). It is also part of the webhook URL path, so it is only ever logged masked.
   * `PUBLIC_BASE_URL`: Required when Turnstile is enabled — the public origin users open to reach the verification page.
   * `DROP_PENDING_UPDATES`: Defaults to `false` so a restart does not silently discard queued updates.
   * `DATABASE_URL`: Relative SQLite paths always resolve against the project root, never the working directory.

### Running the Bot
Make sure your virtual environment is active and run:
```bash
python -m bot
```

### Tests
```bash
pip install pytest pytest-asyncio
pytest
```
The suite covers timezone handling, duplicate and concurrent entries, channel enforcement, the Turnstile form/proof/deep-link flow, concurrent draws and transaction rollback, notification retries, blacklist removal, reroll rules, and locale key parity. No Telegram or Cloudflare network access is required.

For captcha-protected giveaways, participants should first send `/start` in a private chat with the bot. The Join button sends a private verification link. Submit the Turnstile form, return to the bot, and press Join to finish entering. Completing the captcha alone does not enter the giveaway.

If a winner DM fails or a restart interrupts delivery, an admin can use `/retry <giveaway_id>` to send undelivered current winner messages. Successfully recorded deliveries and superseded winners are excluded. Public announcements are not retried by this command. A crash between Telegram accepting a message and the delivery record being committed can still cause a duplicate DM on retry.

---

## 中文

这是一个高度可定制的轻量级 Telegram 抽奖机器人，基于 **Python 3.11+**、**aiogram v3** 和 **SQLAlchemy** 开发。支持持久化定时开奖、频道订阅校验、多语言本地化以及可选的 Cloudflare Turnstile 人机验证。

### 功能特性
* **双语支持**：用户可自由切换语言（英文 & 简体中文）。
* **两种运行模式**：支持公网 Webhook 模式或本地开发测试的 Long Polling（长轮询）模式（无需公网 IP 即可运行）。
* **持久化定时开奖**：内置 SQLite 数据库，`APScheduler` 任务在重启后按数据库里的未开奖记录自动恢复，不会因为重启丢任务。
* **加群/加频道限制**：报名时实时调用 `get_chat_member` 校验，用户没有真正加入所列频道会被拒绝（检查失败时按「未加入」处理，不放行）。
* **反作弊/防脚本**：可选启用 Cloudflare Turnstile。验证通过后会签发一次性凭据，绑定「一个用户 + 一个抽奖」，10 分钟过期；未配置完整时向导会直接拒绝勾选，而不会再发一个什么都不校验的盾牌图标。
* **管理面板**：`/newgiveaway` 对话式向导，另有 `/list`、`/cancel`、`/reroll`、`/retry`、`/ban`、`/unban`、`/addadmin`、`/admins`。
* **开奖可追溯**：使用 `secrets.SystemRandom()` 抽奖，同一人不会跨奖项重复中奖；`/reroll` 只对已结束抽奖生效并保留被替换的旧记录；中奖私信的成功/失败都会入库。
* **健康检查**：两种模式都会由内建 aiohttp 服务提供 `GET /healthz`。

### 安装与运行
1. **克隆仓库**：
   ```bash
   git clone https://github.com/yourusername/tg-giveaway-bot.git
   cd tg-giveaway-bot
   ```

2. **创建虚拟环境**：
   ```bash
   python -m venv .venv
   # Windows 下激活:
   .venv\Scripts\activate
   # Linux/macOS 下激活:
   source .venv/bin/activate
   ```

3. **安装依赖**：
   ```bash
   pip install -r requirements.txt
   ```

4. **配置环境变量**：
   将项目根目录下的 `.env.example` 复制一份并重命名为 `.env`，然后填写必要字段：
   * `BOT_TOKEN`：从 [@BotFather](https://t.me/BotFather) 获取的机器人 Token。
   * `ADMIN_IDS`：管理员的 Telegram 用户 ID（多个用英文逗号分隔，也支持 `[123456,789012]` 格式的 JSON 数组）。
   * `WEBHOOK_BASE_URL`：Webhook 公网 HTTPS 地址（不含尾斜杠）。**留空则自动启动本地 Long Polling 轮询模式**。
   * `WEBHOOK_SECRET`：**仅 Webhook 模式必填**（该模式下缺失会启动即报错）。它同时出现在 webhook URL 路径中，日志里只打印打码形式。生成：`python -c "import secrets; print(secrets.token_urlsafe(32))"`。
   * `PUBLIC_BASE_URL`：开启人机验证时必填，是用户打开验证页的公网地址。
   * `DROP_PENDING_UPDATES`：默认 `false`，避免重启静默吞掉用户刚点的按钮。
   * `DATABASE_URL`：SQLite 相对路径始终相对于项目根目录解析，不受启动目录影响。

5. **启动项目**：
   ```bash
   python -m bot
   ```

6. **运行测试**：
   ```bash
   pip install pytest pytest-asyncio
   pytest
   ```
   覆盖时区处理、重复与并发报名、频道校验、验证码表单及返回机器人流程、凭据原子消费、并发开奖及失败回滚、通知重试、拉黑清理、重抽规则与中英文案键对齐，全程不访问 Telegram 或 Cloudflare 网络。

开启验证码的抽奖：用户应先私聊机器人发送 `/start`，再点击抽奖的参加按钮接收验证链接。提交验证码表单后返回机器人，再点击参加按钮才算完成报名。机器人必须是每个必加频道/群的管理员。

中奖私信发送失败或重启中断时，管理员可使用 `/retry <抽奖id>` 补发。已确认送达及被重抽替换的中奖记录不会补发；该命令不重发频道公告。如果 Telegram 已接收私信、但进程在记录送达前崩溃，补发仍可能产生重复私信。

### 机器人常用命令
* `/start` - 启动机器人并注册账户
* `/help` - 查看命令指南
* `/lang` - 选择切换语言
* `/me` - 查看自己近期的参与记录
* `/newgiveaway` - (管理员) 开始对话式流式创建新抽奖
* `/list` - (管理员) 列出所有进行中的抽奖
* `/cancel <id>` - (管理员) 取消指定 ID 的抽奖（也可以点创建成功后的取消按钮）
* `/reroll <id> <奖项名称>` - (管理员) 对**已结束**抽奖的某一奖项重新抽奖，旧记录保留
* `/retry <id>` - (管理员) 补发该抽奖尚未送达或发送失败的中奖私信
* `/ban <user_id> [原因]` - (管理员) 拉黑某用户，并清除其全部报名记录
* `/unban <user_id>` - (管理员) 解除用户黑名单限制
* `/addadmin <user_id>` - (管理员) 动态授权新的管理员
* `/admins` - (管理员) 查看当前管理员列表
