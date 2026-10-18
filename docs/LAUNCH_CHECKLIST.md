# 공개(런치) 체크리스트

이 문서는 **목록과 절차만** 적습니다. 아래 항목의 실제 제출·배포·게시는 아직 하나도 하지 않았고, 계정이 걸린 일은 본인 명의로 직접 진행합니다. 용어는 문서 끝 용어표를 참고하세요.

## 0. 공개 전 점검 (제출보다 먼저)

- [ ] `uv run pytest` 통과 (HTTP는 전부 mock이라 네트워크·키 없이 돈다)
- [ ] 모의투자(paper) 키로 `get_balance` 실호출 1회 확인 (이 레포 작업에서는 실전 키뿐이라 mock 시험으로만 검증함)
- [ ] 비밀 스캔: 추적 파일에 `.env`·키·계좌번호·개인 경로·이메일이 없는지 확인 (`git ls-files`, `git grep`)
- [ ] GitHub 레포 설명(description)과 토픽(`mcp`, `korea`, `stock`, `kis`, `opendart`, `ipo`) 설정
- [ ] 첫 릴리스: 태그 `v0.1.0` + GitHub Release 노트 (README의 `git+https://...` 실행 명령이 태그로 고정되도록 `@v0.1.0` 안내 추가 검토)
- [ ] 면책 문구가 README 상단·서버 지침(instructions)·응답(`notice`)에 모두 있는지 최종 확인

## 1. PyPI(파이썬 패키지 저장소) 배포 — 사용자 승인 필요

AI가 직접 실행하지 않습니다.

- [ ] PyPI 계정과 2단계 인증 준비, 프로젝트 이름 `kstock-mcp` 선점 가능 여부 확인
- [ ] `uv build`로 sdist/wheel 생성 후 내용 확인 (비밀·불필요 파일 없는지)
- [ ] 가능하면 Trusted Publishing(GitHub Actions가 토큰 없이 배포하는 방식) 사용
- [ ] 배포 후 `uvx kstock-mcp --version`으로 확인하고 README의 설치 명령을 `uvx kstock-mcp` 한 줄로 갱신

## 2. 마켓 4곳 등록

공통 준비물: 한 줄 소개, 도구 7종 표, 면책 문구, 스크린샷 또는 짧은 시연 영상, 레포 URL.

| 마켓 | 제출 경로 | 이 레포의 관련 파일 | 확인할 점 |
|---|---|---|---|
| Smithery | smithery.ai/new | `manifest.json`(MCPB 번들), `smithery.yaml`+`Dockerfile`(구형 컨테이너 방식) | 2026-10 기준 Smithery 문서는 로컬 stdio 서버를 MCPB 번들로 올리는 방식을 안내함. `smithery.yaml`이 아직 쓰이는지 제출 화면에서 확인 |
| Glama | glama.ai 의 MCP 서버 추가 | `Dockerfile`, `README.md` | 서버를 컨테이너로 빌드해 점검하는 방식이면 `Dockerfile`이 쓰임. 정확한 절차는 제출 화면에서 확인 |
| mcp.so | mcp.so 의 Submit | 레포 URL, `README.md` | 제출 양식 항목 확인 |
| Apify | apify.com 의 Store 게시 | (별도 래퍼 필요 가능) | Apify는 Actor 단위로 게시하므로 MCP 서버를 Actor로 감싸는 작업이 필요한지 먼저 확인 |

- [ ] 각 마켓의 약관·심사 기준(금융 정보 서비스 관련 제한 여부 포함) 확인
- [ ] 등록 후 각 페이지에서 설치 명령이 실제로 동작하는지 확인

## 3. Claude Desktop 확장(DXT, 현재 이름 MCPB) 패키징

- [ ] 공식 mcpb 도구로 `manifest.json` 검증 (이 레포에서는 JSON 문법과 도구 목록·버전 일치만 확인했고, 공식 검증기는 실행하지 않음)
- [ ] `.mcpbignore`를 반영해 번들 생성 후 Claude Desktop에서 설치·키 입력 화면·도구 호출 시험
- [ ] `manifest.json`의 `privacy_policies`는 README의 Privacy 절을 가리킴. 디렉터리 심사에서 KIS·DART 공식 개인정보 처리방침 URL을 요구하면 교체

## 4. 커뮤니티 공개

- [ ] 퀀트 커뮤니티, Reddit r/mcp, 개발자 커뮤니티에 소개 글 (디시 주갤은 제외)
- [ ] 글 핵심: 조회 전용, 기본 모의투자, 추천 기능 없음, 키가 로컬에서만 쓰임, 공모주 일정과 균등배정 계산 포함
- [ ] 투자 권유로 읽힐 표현(수익 보장, 종목 언급 등) 금지
- [ ] 각 커뮤니티의 홍보 글 규칙 확인 후 게시 (계정과 글은 본인 명의)
- [ ] Threads 제작기 연재(#47)용 소재 정리

## 5. 공개 후 관찰과 중단 기준 (Kill Criteria)

- [ ] 공개일을 기록하고 매주 GitHub 스타와 설치 수를 기록
- [ ] **공개 후 4주 시점에 스타 30 미만이고 주간 설치 50 미만이면** 유지보수 모드로 전환하고 2호기(카페24 앱)로 이동 (매몰비용 금지)
- [ ] 이슈·피드백 반영 목록과 v0.2(조건검색, 포트폴리오 리포트, 공모주 알림, 가드가 있는 주문) 설계

## 용어표

| 용어 | 뜻 |
|---|---|
| MCP | Model Context Protocol. AI 에이전트가 외부 도구·데이터를 쓰는 표준 규약 |
| stdio | 표준 입출력. 서버를 내 컴퓨터의 자식 프로세스로 띄워 대화하는 방식 |
| DXT / MCPB | Claude Desktop 확장 패키지 형식. DXT가 MCPB(MCP Bundle)로 이름이 바뀜 |
| PyPI | 파이썬 패키지 공식 저장소 |
| Trusted Publishing | 장기 토큰 없이 CI가 PyPI에 배포하도록 신뢰 관계를 맺는 방식 |
| Smithery / Glama / mcp.so / Apify | MCP 서버를 찾고 설치하는 마켓·디렉터리 |
| Kill Criteria | 지표가 기준에 못 미치면 프로젝트를 접거나 줄이는 사전 약속 |
