"""Allow running as: python -m psp_host"""

# 必须在导入 main（间接导入 gi）之前挂载捆绑运行时（冻结打包支持）
from .runtime_env import bootstrap  # noqa: E402

bootstrap()

from .main import main  # noqa: E402

main()
