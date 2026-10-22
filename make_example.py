"""Tạo example.mp4: video test pattern + giọng TTS tiếng Trung ~40 giây."""
import asyncio
import subprocess
import sys
from pathlib import Path

CHINESE_TEXT = (
    "大家好，欢迎来到今天的节目。"
    "今天我们要讨论一个非常有趣的话题，关于人工智能和未来科技的发展。"
    "随着技术的不断进步，我们的生活也在发生着巨大的变化。"
    "智能手机、自动驾驶汽车、还有各种各样的智能设备，已经成为了我们日常生活的一部分。"
    "在未来，人工智能将会在医疗、教育、交通等各个领域发挥更加重要的作用。"
    "我们应该积极拥抱这些变化，同时也要注意保护我们的隐私和安全。"
    "谢谢大家的收听，我们下次再见。"
)

VOICE = "zh-CN-XiaoxiaoNeural"

async def make_audio(text: str, out: Path) -> None:
    import edge_tts
    communicate = edge_tts.Communicate(text, VOICE)
    await communicate.save(str(out))
    print(f"Audio: {out}  ({out.stat().st_size // 1024} KB)")

def make_video(audio: Path, out: Path, ffmpeg: str) -> None:
    pass  # logic moved to __main__

if __name__ == "__main__":
    import shutil
    # Lấy path ffmpeg/ffprobe từ hàm locator để đảm bảo tìm đúng
    from dubber.core.utils.ffmpeg_locator import find_ffmpeg, find_ffprobe
    _ffmpeg  = str(find_ffmpeg())
    _ffprobe = str(find_ffprobe())

    root  = Path(__file__).parent
    audio = root / "example_audio.mp3"
    out   = root / "example.mp4"

    asyncio.run(make_audio(CHINESE_TEXT, audio))

    # Dùng ffprobe trực tiếp thay vì derive từ ffmpeg path
    probe = subprocess.run(
        [_ffprobe, "-v", "quiet",
         "-show_entries", "format=duration", "-of", "csv=p=0", str(audio)],
        capture_output=True, text=True,
    )
    duration = float(probe.stdout.strip() or "40")
    print(f"Duration: {duration:.1f}s")

    cmd = [
        _ffmpeg, "-y",
        "-f", "lavfi",
        "-i", f"testsrc2=duration={duration}:size=854x480:rate=25",
        "-i", str(audio),
        "-c:v", "libx264", "-crf", "28", "-preset", "fast",
        "-c:a", "aac", "-b:a", "128k",
        "-shortest",
        str(out),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stderr[-1000:])
        sys.exit(1)
    print(f"Video: {out}  ({out.stat().st_size // 1024} KB)")
    audio.unlink(missing_ok=True)
    print("Done →", out)
