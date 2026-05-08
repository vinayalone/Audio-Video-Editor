"""
Telegram Media Trimmer Bot
Supports: Video, Video Note (circles), Audio, Voice messages
Usage: Send any supported media -> bot asks for start/end times -> get trimmed file back

Railway deployment: set BOT_TOKEN in your project's Variables tab.
"""

import os
import logging
import subprocess
import tempfile
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

# ── Config ────────────────────────────────────────────────────────────────────
# Railway injects environment variables automatically.
# Set BOT_TOKEN in your Railway project: Settings -> Variables -> New Variable
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
if not BOT_TOKEN:
    raise RuntimeError(
        "BOT_TOKEN is not set. "
        "Add it in your Railway project: Settings -> Variables -> BOT_TOKEN"
    )

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s | %(levelname)-8s | %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    level=logging.INFO,
)
logger = logging.getLogger("TrimBot")

# ── Conversation state ────────────────────────────────────────────────────────
WAITING_FOR_TIMES = 1


# ── Helpers ───────────────────────────────────────────────────────────────────

def parse_time(s: str) -> float:
    """
    Parse a time string to seconds.
    Accepted formats: HH:MM:SS  |  MM:SS  |  plain seconds (int or float)
    """
    s = s.strip()
    parts = s.split(":")
    try:
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        elif len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
        else:
            return float(s)
    except ValueError:
        raise ValueError(f"Cannot parse time: {s!r}")


def seconds_to_hms(seconds: float) -> str:
    """Format seconds as H:MM:SS.ss for display."""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def build_ffmpeg_cmd(
    input_path: str,
    output_path: str,
    start: float,
    duration: float,
    media_type: str,
) -> list:
    """
    Build the ffmpeg command for trimming.
    - video / video_note : re-encode with libx264 for frame-accurate cuts.
    - audio / voice      : stream-copy (fast, no quality loss).
    """
    base = ["ffmpeg", "-y", "-ss", str(start), "-i", input_path, "-t", str(duration)]

    if media_type in ("video", "video_note"):
        return base + [
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "23",
            "-c:a", "aac",
            "-b:a", "128k",
            "-movflags", "+faststart",
            output_path,
        ]
    else:
        return base + ["-c", "copy", output_path]


# ── Handlers ──────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "✂️ *Media Trimmer Bot*\n\n"
        "Send me any of these and I'll trim it:\n"
        "• 🎬 Video\n"
        "• ⭕ Video Note (circle)\n"
        "• 🎵 Audio\n"
        "• 🎙 Voice message\n\n"
        "Just send the file to get started!",
        parse_mode="Markdown",
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "✂️ *How to use*\n\n"
        "1. Send me a video, video note, audio, or voice message.\n"
        "2. I'll ask for trim times.\n"
        "3. Reply with: `start_time end_time`\n\n"
        "*Time formats accepted:*\n"
        "• `MM:SS`      e.g. `0:10 1:45`\n"
        "• `HH:MM:SS`   e.g. `0:00:10 0:01:45`\n"
        "• Seconds      e.g. `10 105`\n\n"
        "*Commands:*\n"
        "/start  — Welcome message\n"
        "/help   — This help text\n"
        "/cancel — Cancel current trim\n\n"
        "⚠️ Files must be under *2 GB* (Telegram limit).\n"
        "⚠️ Video notes are capped at *60 s* by Telegram.",
        parse_mode="Markdown",
    )


async def handle_media(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Entry point: user sends a media file."""
    msg = update.message

    if msg.video:
        media_type, ext, label = "video", ".mp4", "🎬 video"
        file_obj = msg.video
    elif msg.video_note:
        media_type, ext, label = "video_note", ".mp4", "⭕ video note"
        file_obj = msg.video_note
    elif msg.audio:
        media_type, ext, label = "audio", ".mp3", "🎵 audio"
        file_obj = msg.audio
    elif msg.voice:
        media_type, ext, label = "voice", ".ogg", "🎙 voice"
        file_obj = msg.voice
    else:
        return ConversationHandler.END

    context.user_data.update(
        file_id=file_obj.file_id,
        media_type=media_type,
        file_ext=ext,
        label=label,
    )

    await msg.reply_text(
        f"Got your {label}! ✅\n\n"
        "Now send me the *start* and *end* times separated by a space:\n"
        "`start_time end_time`\n\n"
        "Examples:\n"
        "• `0:10 1:30`      — 10 s to 1 min 30 s\n"
        "• `0:00:30 0:02:15` — HH:MM:SS format\n"
        "• `30 135`          — plain seconds\n\n"
        "Send /cancel to abort.",
        parse_mode="Markdown",
    )
    return WAITING_FOR_TIMES


async def do_trim(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """User replied with trim times — download, trim, send back."""
    text = update.message.text.strip()
    parts = text.split()

    if len(parts) != 2:
        await update.message.reply_text(
            "❌ Please send *exactly two* times separated by a space.\n"
            "Example: `0:10 1:30`",
            parse_mode="Markdown",
        )
        return WAITING_FOR_TIMES

    try:
        start = parse_time(parts[0])
        end = parse_time(parts[1])
    except ValueError as exc:
        await update.message.reply_text(
            f"❌ Invalid time: {exc}\n"
            "Use `MM:SS`, `HH:MM:SS`, or plain seconds.",
            parse_mode="Markdown",
        )
        return WAITING_FOR_TIMES

    if start < 0:
        await update.message.reply_text("❌ Start time cannot be negative.")
        return WAITING_FOR_TIMES

    if start >= end:
        await update.message.reply_text(
            "❌ Start time must be *before* end time.", parse_mode="Markdown"
        )
        return WAITING_FOR_TIMES

    duration = end - start
    file_id = context.user_data["file_id"]
    media_type = context.user_data["media_type"]
    file_ext = context.user_data["file_ext"]
    label = context.user_data["label"]

    status_msg = await update.message.reply_text("⏳ Downloading and trimming… please wait.")

    try:
        tg_file = await context.bot.get_file(file_id)

        with tempfile.TemporaryDirectory() as tmpdir:
            input_path = os.path.join(tmpdir, "input" + file_ext)
            output_path = os.path.join(tmpdir, "output" + file_ext)

            await tg_file.download_to_drive(input_path)
            logger.info(
                "Downloaded %s (%.1f MB)",
                label,
                os.path.getsize(input_path) / 1_048_576,
            )

            cmd = build_ffmpeg_cmd(input_path, output_path, start, duration, media_type)
            logger.info("Running: %s", " ".join(cmd))
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

            if result.returncode != 0:
                logger.error("ffmpeg stderr:\n%s", result.stderr)
                await status_msg.edit_text(
                    "❌ Trimming failed. Make sure the times are within the media length."
                )
                return ConversationHandler.END

            logger.info(
                "Trimmed file: %.2f MB",
                os.path.getsize(output_path) / 1_048_576,
            )
            caption = (
                f"✅ Trimmed {label}\n"
                f"⏱ {seconds_to_hms(start)} → {seconds_to_hms(end)} "
                f"({seconds_to_hms(duration)} long)"
            )

            with open(output_path, "rb") as f:
                if media_type == "video":
                    await update.message.reply_video(video=f, caption=caption)
                elif media_type == "video_note":
                    await update.message.reply_video_note(video_note=f)
                elif media_type == "audio":
                    await update.message.reply_audio(audio=f, caption=caption)
                elif media_type == "voice":
                    await update.message.reply_voice(voice=f, caption=caption)

        await status_msg.delete()
        logger.info("Trim complete for user %s", update.effective_user.id)

    except subprocess.TimeoutExpired:
        await status_msg.edit_text(
            "❌ Timed out — file may be too large. Try a shorter clip."
        )
    except Exception as exc:
        logger.exception("Unexpected error during trim")
        await status_msg.edit_text(f"❌ Unexpected error: {exc}")

    context.user_data.clear()
    return ConversationHandler.END


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text("❌ Cancelled. Send a new media file whenever you're ready.")
    return ConversationHandler.END


async def err_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Unhandled exception", exc_info=context.error)


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    app = Application.builder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[
            MessageHandler(
                filters.VIDEO | filters.VIDEO_NOTE | filters.AUDIO | filters.VOICE,
                handle_media,
            )
        ],
        states={
            WAITING_FOR_TIMES: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, do_trim)
            ]
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
        allow_reentry=True,
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(conv)
    app.add_error_handler(err_handler)

    logger.info("Bot is running — press Ctrl+C to stop.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
