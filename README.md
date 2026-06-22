# Telegram Giveaway Bot / 电报抽奖机器人

[English](#english) | [中文](#中文)

---

## English

A highly customizable and lightweight Telegram giveaway bot built with **Python 3.11+**, **aiogram v3**, and **SQLAlchemy**. It supports persistent scheduling, channel-membership restrictions, multilingual localization, and optional Cloudflare Turnstile anti-bot verification.

### Features
* **Bilingual Support**: User-switchable languages (English and Simplified Chinese).
* **Multiple Modes**: Support for public HTTPS Webhook or local/simple Long Polling mode.
* **Persistent Scheduling**: Built-in SQLite database with `APScheduler` guarantees no lost giveaways on bot restarts.
* **Required Channels**: Restrict entries to users subscribed to designated channels.
* **Anti-Bot Verification**: Optional Cloudflare Turnstile CAPTCHA verification to prevent automated entries.
* **Admin Tools**: Simple in-chat command-based dashboard to create, manage, cancel, or re-roll giveaways.

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
   git clone https://github.com/Star-Dream-AI/telegram-giveaway-bot.git
   cd telegram-giveaway-bot
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
   * `ADMIN_IDS`: Comma-separated Telegram User IDs of administrators.
   * `WEBHOOK_BASE_URL`: (Optional) Public URL (e.g. `https://giveaway.example.com`) for webhook mode. Leave blank to run the bot in **Long Polling** mode.
   * `WEB_HOST` / `WEB_PORT`: Server bind configuration (Defaults to `127.0.0.1:8080`).
   * `WEBHOOK_SECRET`: Secure string for validating Telegram callbacks.

### Running the Bot
Make sure your virtual environment is active and run:
```bash
python -m bot
```

---

## 中文

这是一个高度可定制的轻量级 Telegram 抽奖机器人，基于 **Python 3.11+**、**aiogram v3** 和 **SQLAlchemy** 开发。支持持久化定时开奖、频道订阅校验、多语言本地化以及可选的 Cloudflare Turnstile 人机验证。

### 功能特性
* **双语支持**：用户可自由切换语言（英文 & 简体中文）。
* **两种运行模式**：支持公网 Webhook 模式或本地开发测试的 Long Polling（长轮询）模式（无需公网 IP 即可运行）。
* **持久化定时开奖**：内置 SQLite 数据库，结合 `APScheduler` 定时任务，保障机器人重启后不会丢失未开奖任务。
* **加群/加频道限制**：可限制只有加入指定频道的用户才能参与抽奖。
* **反作弊/防脚本**：可选启用 Cloudflare Turnstile 验证码，管理员可一键要求强制人机验证。
* **管理面板**：提供命令式的管理员交互流程，轻松创建、管理、取消或重抽奖品。

### 安装与运行
1. **克隆仓库**：
   ```bash
   git clone https://github.com/Star-Dream-AI/telegram-giveaway-bot.git
   cd telegram-giveaway-bot
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
   * `ADMIN_IDS`：管理员的 Telegram 用户 ID（多个用英文逗号分隔）。
   * `WEBHOOK_BASE_URL`：Webhook 公网 HTTPS 地址。**留空则自动启动本地 Long Polling 轮询模式**。
   * `WEBHOOK_SECRET`：Webhook 校验密钥，可使用命令行生成：`python -c "import secrets; print(secrets.token_urlsafe(32))"`。

5. **启动项目**：
   ```bash
   python -m bot
   ```

### 机器人常用命令
* `/start` - 启动机器人并注册账户
* `/help` - 查看命令指南
* `/lang` - 选择切换语言
* `/me` - 查看自己近期的参与记录
* `/newgiveaway` - (管理员) 开始对话式流式创建新抽奖
* `/list` - (管理员) 列出所有进行中的抽奖
* `/cancel <id>` - (管理员) 取消指定 ID 的抽奖
* `/reroll <id> <等奖名称>` - (管理员) 对指定抽奖的某一等级奖项重新抽奖
* `/ban <user_id> [原因]` - (管理员) 拉黑某用户使其无法参与抽奖
* `/unban <user_id>` - (管理员) 解除用户黑名单限制
* `/addadmin <user_id>` - (管理员) 动态授权新的管理员
