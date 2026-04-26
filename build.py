import shutil
import subprocess
import sys
from pathlib import Path


def remove_path(path: Path):
    """删除文件或文件夹；不存在时直接跳过。"""
    if not path.exists():
        return
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()


def main():
    project_root = Path(__file__).resolve().parent
    dist_dir = project_root / "dist"
    build_dir = project_root / "build"
    spec_file = project_root / "TG Sticker Maker.spec"
    main_file = project_root / "main.py"
    resources_dir = project_root / "resources"
    bin_dir = project_root / "bin"
    output_dir = dist_dir / "TG Sticker Maker"

    # 优先使用用户期望的图标名；如果当前项目里还是双后缀图标，也兼容一下。
    icon_file = resources_dir / "app_icon.png"
    if not icon_file.exists():
        fallback_icon = resources_dir / "app_icon.png.png"
        if fallback_icon.exists():
            icon_file = fallback_icon
        else:
            raise FileNotFoundError("未找到 resources/app_icon.png 图标文件。")

    if not main_file.exists():
        raise FileNotFoundError("未找到 main.py，无法执行打包。")

    if not resources_dir.exists():
        raise FileNotFoundError("未找到 resources 文件夹，无法执行打包。")

    if not bin_dir.exists():
        raise FileNotFoundError("未找到 bin 文件夹，无法复制 FFmpeg 资源。")

    # 1. 环境准备：删除旧的 dist 和 build 文件夹
    remove_path(dist_dir)
    remove_path(build_dir)

    # 2. 执行 PyInstaller 打包
    pyinstaller_cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--onedir",
        "--noconsole",
        f"--icon={icon_file}",
        '--name=TG Sticker Maker',
        f"--add-data={resources_dir};resources",
        str(main_file),
    ]

    print("正在执行 PyInstaller 打包，请稍候...")
    subprocess.run(pyinstaller_cmd, cwd=project_root, check=True)

    # 3. 资源整合：将 bin 文件夹复制到 dist/TG Sticker Maker/ 中
    if not output_dir.exists():
        raise FileNotFoundError(f"打包完成后未找到输出目录：{output_dir}")

    target_bin_dir = output_dir / "bin"
    remove_path(target_bin_dir)
    shutil.copytree(bin_dir, target_bin_dir)

    # 4. 清理工作：删除 .spec 文件和 build 文件夹
    remove_path(spec_file)
    remove_path(build_dir)

    # 5. 成功提示
    print("打包成功！绿色版程序位于 dist/TG Sticker Maker 文件夹中。")


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as exc:
        print(f"打包失败，PyInstaller 返回码：{exc.returncode}")
        sys.exit(exc.returncode)
    except Exception as exc:
        print(f"打包失败：{exc}")
        sys.exit(1)
