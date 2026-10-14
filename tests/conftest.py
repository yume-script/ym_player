"""저장소 루트를 import 경로에 넣어 `import ym_player`가 설치 없이 되게 한다."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
