# K-Stock MCP

[English README](README.md)

한국 주식시장을 Claude 등 MCP(Model Context Protocol, AI 에이전트가 외부 도구를 쓰는 표준 규약) 에이전트에 연결하는 **조회 전용** 서버입니다. **한국투자증권(KIS) Open API 시세·차트·잔고, OpenDART 공시·재무, 공모주 일정과 균등배정 계산**을 제공합니다.

- KIS 인증 자동화: 토큰을 한 번 발급해 디스크에 캐시(유효 24시간)하고, 모의투자와 실전을 분리하며 **기본값은 모의투자(paper)** 입니다.
- 데이터와 계산만 제공합니다. **주문 도구 없음. 매수/매도 추천 없음.**
- stdio 방식, `uvx`로 실행, 호스팅 서비스 없음: 키가 내 컴퓨터 밖으로 나가지 않습니다.

> **면책.** 이 서버가 돌려주는 모든 내용은 참고용 정보이며 투자 권유나 자문이 아닙니다. 투자자문업·일임업이 아니며 특정 종목을 추천하지 않습니다. 투자 판단과 그 결과의 책임은 전적으로 사용자에게 있습니다. 데이터는 지연·누락·오류가 있을 수 있습니다. 한국투자증권·금융감독원과 무관한 개인 프로젝트입니다.

## 도구 (v0.1, 전부 조회 전용)

| 도구 | 기능 | 데이터 출처 |
|---|---|---|
| `search_stock` | 종목명/코드로 코드·시장·유형 검색 | KIS 공개 종목마스터 파일 |
| `get_quote` | 현재가, 당일 고저, 거래량, PER/PBR/EPS/BPS, 10단계 호가 | KIS `inquire-price`, `inquire-asking-price-exp-ccn` |
| `get_ohlcv` | 일/주/월/년봉 (수정주가 선택) | KIS `inquire-daily-itemchartprice` |
| `get_balance` | 보유종목과 계좌 요약 (기본 모의투자) | KIS `inquire-balance` |
| `get_ipo_schedule` | 공모주 청약일·공모가·주관사, 선택적 균등배정 예상 | OpenDART 증권신고서(지분증권) |
| `get_disclosure` | 최근 공시 목록과 DART 뷰어 링크 | OpenDART `list.json` |
| `get_financials` | 요약 재무(자산·부채·자본·매출·영업이익·순이익) | OpenDART `fnlttSinglAcnt.json` |

모든 응답에 짧은 면책 문구(`notice`)가 들어가며, 서버 지침(instructions)이 모델에게 조언처럼 제시하지 말라고 안내합니다.

## 설치 3단계

**1. 키 발급** (둘 다 무료)

- KIS Open API 앱키/시크릿: [apiportal.koreainvestment.com](https://apiportal.koreainvestment.com). 먼저 **모의투자 키**로 시작하세요(실전 키와 별도입니다).
- OpenDART 인증키: [opendart.fss.or.kr](https://opendart.fss.or.kr).

**2. [uv](https://docs.astral.sh/uv/) 설치 후 실행 확인**

```bash
uvx --from git+https://github.com/ez2sarang/kstock-mcp kstock-mcp --version
```

(PyPI 배포 후에는 `uvx kstock-mcp` 한 줄이 됩니다.)

**3. 클라이언트에 등록**

Claude Code:

```bash
claude mcp add kstock \
  --env KIS_ENV=paper \
  --env KIS_APP_KEY=발급받은-앱키 \
  --env KIS_APP_SECRET=발급받은-시크릿 \
  --env KIS_ACCOUNT_NO=12345678-01 \
  --env DART_API_KEY=발급받은-DART-키 \
  -- uvx --from git+https://github.com/ez2sarang/kstock-mcp kstock-mcp
```

Claude Desktop (`claude_desktop_config.json`, 앱이 `uvx`를 못 찾으면 전체 경로를 쓰세요):

```json
{
  "mcpServers": {
    "kstock": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/ez2sarang/kstock-mcp", "kstock-mcp"],
      "env": {
        "KIS_ENV": "paper",
        "KIS_APP_KEY": "발급받은-앱키",
        "KIS_APP_SECRET": "발급받은-시크릿",
        "KIS_ACCOUNT_NO": "12345678-01",
        "DART_API_KEY": "발급받은-DART-키"
      }
    }
  }
}
```

쓰는 도구에 필요한 키만 있으면 됩니다. 공시·재무·공모주는 `DART_API_KEY`, 시세·차트는 KIS 변수, `get_balance`는 계좌번호까지, `search_stock`은 키가 필요 없습니다.

## 설정

| 환경변수 | 의미 |
|---|---|
| `KIS_ENV` | `paper`(기본) 또는 `real`. 다른 값은 실전으로 오해하지 않고 오류로 거부합니다 |
| `KIS_APP_KEY`, `KIS_APP_SECRET` | 선택한 환경의 KIS 인증 정보 |
| `KIS_ACCOUNT_NO` | `12345678-01`, `1234567801`, `12345678`(상품코드 기본 `01`). 또는 `KIS_CANO` + `KIS_ACNT_PRDT_CD` |
| `DART_API_KEY` | OpenDART 인증키 |
| `KSTOCK_MCP_CACHE_DIR` | 캐시 위치(기본 `~/.cache/kstock-mcp`): KIS 토큰(권한 0600), 종목마스터(1일), DART 회사코드(7일) |

## 공모주 일정과 균등배정

출처 선택: `get_ipo_schedule`은 OpenDART 증권신고서(지분증권)를 씁니다. 공식·문서화된 키 기반 API라서 크롤링과 robots.txt·약관 문제가 없기 때문입니다. 증권신고서를 낸 미상장 발행사를 공모주 후보로 봅니다. 일정·가격·주관사는 최신 신고서(정정 포함) 기준이며 수요예측이 끝나기 전까지 바뀔 수 있습니다. 신고서에는 일반청약자 배정비율과 청약자 수가 없습니다.

`expected_subscribers`(예상 청약자 수)와 선택 인자(`general_ratio_pct`, `equal_ratio_pct`, `min_subscription_shares`, `deposit_rate_pct`)를 넘기면 공모주마다 `equal_allocation_estimate`가 붙습니다. 계산은 순수 함수(`kstock_mcp.allocation.estimate_equal_allocation`)이고 응답에 산식과 가정이 그대로 나옵니다.

```
일반청약자 물량   = floor(총공모주식수 x 일반청약자 배정비율 / 100)
균등배정 물량     = floor(일반청약자 물량 x 균등 비율 / 100)
1인 기본 배정     = floor(균등배정 물량 / 예상 청약자 수)
잔여분(추첨)      = 균등배정 물량 mod 예상 청약자 수
```

실제 배정은 증권사(인수인)별로 따로 이뤄지므로 대략적인 추정치로만 보세요.

## 개인정보

서버는 내 컴퓨터에서 stdio로만 동작합니다. KIS 키/시크릿은 KIS Open API로, DART 키는 OpenDART로만 전송되고, KIS의 공개 종목마스터 파일을 내려받습니다. 텔레메트리(사용 정보 수집)와 별도 서버가 없습니다. 키는 환경변수에서만 읽고 로그에 남기지 않으며, 오류 메시지에도 요청 URL이 포함되지 않습니다.

## 개발

```bash
git clone https://github.com/ez2sarang/kstock-mcp && cd kstock-mcp
uv sync
uv run pytest        # HTTP는 전부 mock, 네트워크·키 불필요
```

의존성은 `pyproject.toml`과 `uv.lock`에 정확한 버전으로 고정되어 있습니다. `mcp` 2.x를 쓰며, 기존 `FastMCP` 클래스가 `MCPServer`로 이름이 바뀌었습니다.

## 로드맵

v0.2 이후: 조건검색, 포트폴리오 리포트, 공모주 알림. 이 서버는 조회 전용으로 유지하며 주문 기능은 계획하지 않습니다.

## 라이선스

[MIT](LICENSE)
