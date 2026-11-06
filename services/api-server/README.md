# REALYTICS API

FastAPI 기반의 문서·입력 검토, 결정론적 계산, Pod 실행, 증거와 보고서 저장을 담당한다.

전체 설치는 [루트 README](../../README.md), 현재 범위는 [구현 현황](../../docs/product/current-implementation-status.md), API 개요는 [API 문서](../../docs/api.md)를 따른다.

이 폴더에서 기존 가상환경으로 실행한다.

```powershell
../../.runtime/api-venv/Scripts/python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000
../../.runtime/api-venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider
```

`/health/live`는 프로세스 상태, `/health/ready`는 구성된 실행 환경의 준비 상태다. 운영 데이터의 접근 주체는 [아키텍처 경계](../../docs/architecture/architecture-boundary.md)를 따른다. 테스트는 `tests/conftest.py`가 격리한 임시 런타임을 사용하며 원본 DB를 테스트 데이터로 쓰지 않는다.

`use_cases`에서 기존 사용자 흐름을 재사용하고, `core`의 계산과 `agents`의 업무 처리를 조합한다. 새로운 Agent를 만들기 전에 기존 호출 경로로 해결할 수 있는지 확인한다. `agents/llm_agent_policies.md`는 런타임이 읽는 프롬프트이므로 일반 설명 문서처럼 삭제·병합하지 않는다.
