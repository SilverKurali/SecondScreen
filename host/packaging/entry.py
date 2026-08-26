"""PyInstaller 冻结打包入口。

直接冻结 ``psp_host/__main__.py`` 会因顶层脚本身份丢失包上下文，
导致相对导入失败；本入口以绝对导入方式启动，供打包使用：

    python -m PyInstaller --onedir packaging/entry.py
"""

from psp_host.runtime_env import bootstrap

bootstrap()

from psp_host.main import main  # noqa: E402

if __name__ == "__main__":
    main()
