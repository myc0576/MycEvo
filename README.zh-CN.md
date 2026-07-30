# MycEvo

![MycEvo 绯荤粺娴佺▼鍥綸(assets/readme/resevo-technical-architecture.svg)

**涓鸿法 Codex銆丆laude Code銆丆ursor 绛?Agent 宸ヤ綔鐨勪汉鎻愪緵鏈湴鈥滃缃伐浣滄祦澶ц剳鈥濄€?*

[English](README.md)

> 鍙戝竷鐘舵€侊細**PaperFrames v0.2.0-rc.1**銆傛湰鍊欓€夌増鏈噰鐢?Apache-2.0锛涚涓夋柟渚濊禆鍜岀礌鏉愪粛閫傜敤鍏跺師濮嬭鍙瘉銆?

Agent 鍙互瀹屾垚涓€娆′换鍔★紝鍗村緢瀹规槗涓㈠け浠诲姟鑳屽悗鐨勬柟娉曪細涓轰粈涔堣繖鏍峰喅绛栥€佸摢浜涜瘉鎹湁鏁堛€佷粈涔堝皾璇曞け璐ャ€佸摢浜涚害鏉熶笉鑳界牬鍧忋€佷笅涓€涓?Agent 蹇呴』澶嶆牳浠€涔堛€侻ycEvo 鎶婅繖浜涚粨鏋滄矇娣€涓烘湰鍦般€佸彲澶嶆煡銆佸彲婕旇繘鐨勫伐浣滄祦璁板繂銆?

瀹冧笉鏄柊鐨勮亰澶╃晫闈紝涔熶笉鏄?Agent 鎵ц鍣ㄣ€傚畠浣嶄簬 Codex銆丆laude Code銆丆ursor 绛夋墽琛屽伐鍏蜂箣涓婏紝璐熻矗绠＄悊锛?

- 宸ヤ綔娴佹敼杩涘€欓€夛紱
- 璇佹嵁銆乨iff銆佸喅绛栧拰 provenance锛?
- 浜哄伐鎺у埗鐨勬檵鍗囷紱
- 璺?Agent 鍙鐢ㄧ殑浜ゆ帴涓婁笅鏂囷紱
- 鍙鏌ャ€佸彲杩佺Щ鐨勬湰鍦?workspace 鐘舵€併€?

## 涓轰粈涔堥渶瑕?MycEvo

```text
Agent A 鎵ц浠诲姟
  -> 璁板綍璇佹嵁鍜屾柟娉曟敼杩涘缓璁?
  -> MycEvo 鍐欏叆 candidate
  -> 浜虹被瀹℃煡璇佹嵁
  -> 鍚庣画 Agent 缁ф壙宸叉帴鍙楃殑涓婁笅鏂?
```

MycEvo 涓嶇粦瀹氭ā鍨嬶紝涓嶅唴缃?LLM锛涚‘瀹氭€?demo 涓嶈姹傞澶栨ā鍨?API key銆?

## 褰撳墠 Technical Preview 瀹為檯鎻愪緵浠€涔?

| 鑳藉姏 | 鍏ュ彛 | 鐘舵€?|
|---|---|---|
| 鍙Щ妞嶆湰鍦?workspace | `mycevo init` | 宸插疄鐜板苟娴嬭瘯 |
| 纭畾鎬?candidate-first loop | `mycevo demo` | 宸插疄鐜板苟娴嬭瘯 |
| 瀹夎涓?workspace 璇婃柇 | `mycevo doctor`銆乣mycevo status` | 宸插疄鐜板苟娴嬭瘯 |
| workspace 鐧昏 | `mycevo workspace` | 宸插疄鐜板苟娴嬭瘯 |
| recall銆乮ntake銆乧loseout銆乪valuation | 鏃х増 source-checkout 鏈嶅姟 | 浠呭吋瀹癸紝涓嶅睘浜?wheel 姝ｅ紡濂戠害 |
| append-only provenance | `mycevo provenance` | 宸插疄鐜板苟娴嬭瘯 |
| Codex / Claude Code MCP 閰嶇疆 | `mycevo mcp install ... --dry-run` | 宸插疄鐜?dry-run 骞舵祴璇?|
| 鐙珛鐨勪汉宸ュ喅绛栦笌 canonical 鏅嬪崌濂戠害 | 鈥?| 灏氭湭浣滀负鍏紑鎺ュ彛瀹屾垚 |
| 瀹屾暣 handoff銆乺ollback銆乮mport銆乪xport銆乨elete 鐢熷懡鍛ㄦ湡 | 鈥?| roadmap锛屼笉瀹ｇО宸插畬鎴?|
| 鍥㈤槦鍗忎綔銆丷BAC銆佸悓姝ャ€佸叡浜?canonical | 鈥?| 鏈潵 Team 浜у搧锛屼笉鍦ㄥ綋鍓嶄粨搴?|

瑙勮寖鐘舵€佷互[鍙戝竷濂戠害鐭╅樀](docs/release/community-release-contract.md)涓哄噯銆傚彧鏈夊崟鐢ㄦ埛鏍稿績寰幆姣忎竴椤归兘杈惧埌 `shipped + tested`锛孧ycEvo 鎵嶄娇鐢?**Community** 鍚嶇О銆?

## 浜斿垎閽熸湰鍦?Demo

闇€瑕?Python 3.10 鎴栨洿楂樼増鏈€?

```powershell
python -m pip install -e .

$workspace = Join-Path $env:TEMP "mycevo-demo"
$env:MYCEVO_USER_ROOT = Join-Path $env:TEMP "mycevo-demo-user"
mycevo --root $workspace init --json
mycevo --root $workspace demo --json
mycevo --root $workspace doctor --json
```

Demo 浼氬啓鍏ヤ竴涓?`pending validation` candidate锛屽苟鏄庣‘杩斿洖 `promotion_performed: false`銆?

娴嬭瘯 Agent 閰嶇疆 dry-run锛?

```powershell
mycevo --root $workspace mcp install codex --dry-run
mycevo --root $workspace mcp install claude --dry-run
```

缁х画闃呰[瀹屾暣 Demo](docs/getting-started/five-minute-demo.md)鍜孾璺?Agent 绀轰緥](examples/cross-agent-handoff/README.md)銆?

## Community 涓庢湭鏉ユ敹璐硅竟鐣?

鍏紑鍗曠敤鎴蜂骇鍝佽礋璐ｆ湰鍦板伐浣滄祦鎹曡幏銆乧andidate銆佽瘉鎹€乸rovenance銆佷汉宸ユ潈闄愩€佸彲绉绘鏍煎紡銆丆LI/MCP銆佸叕鍏?pack 鍜屽畨鍏ㄤ慨澶嶃€?

鏈潵 Team 鐨勪粯璐逛环鍊间粠澶氫汉鍗忎綔澶嶆潅搴﹀紑濮嬶細鎴愬憳銆佽鑹层€佸叡浜?canonical銆乺eview queue銆佸浜哄鎵广€佸悓姝ャ€佸啿绐佸鐞嗐€佸洟闃熷璁″拰绠＄悊鍚庡彴銆?

MycEvo 涓嶈鍒掓妸鐢ㄦ埛鏁版嵁瀵煎嚭/鍒犻櫎銆佸崟鐢ㄦ埛 provenance銆佷汉宸ユ檵鍗囨潈銆佸畨鍏ㄤ慨澶嶅拰鍏紑鏍煎紡鍏煎鍋氭垚寮哄埗浠樿垂澧欍€傝瑙?[Community / Team 杈圭晫](docs/product/community-team-boundary.md)銆?

## 鏋舵瀯杈圭晫

```mermaid
flowchart LR
  A["Codex / Claude Code / Cursor"] --> B["CLI 鎴?stdio MCP"]
  B --> C["MycEvo public engine"]
  C --> D["鏈湴 workspace"]
  D --> E["Candidate + evidence + provenance"]
  E --> F["浜哄伐鍐崇瓥"]
```


## 涓庣浉閭诲伐鍏风殑鍖哄埆

- Agent runtime 璐熻矗鎵ц锛汳ycEvo 璐熻矗鏂规硶鐨勬矇娣€銆佸鏍稿拰婕旇繘銆?
- Dify銆乶8n銆丗lowise 璐熻矗搴旂敤鎴栬嚜鍔ㄥ寲缂栨帓锛汳ycEvo 璁板綍涓轰粈涔堣鏀瑰伐浣滄祦銆佽瘉鎹槸鍚︽敮鎸佷慨鏀广€?
- Langfuse銆丩angSmith 鍋忓悜妯″瀷涓庡簲鐢?trace锛汳ycEvo 杩樻不鐞嗛潪 LLM 璧勪骇銆佸喅绛栥€佸€欓€夊拰璺?Agent 浜ゆ帴銆?

## 鎹曡幏瀹屾暣搴?

- **L0 portable锛?*浠讳綍 Agent 閮借兘浣跨敤鐨勬枃浠跺拰鍛戒护鍗忚銆?
- **L1 verified adapter锛?*閫氳繃娴嬭瘯鐨勯厤缃垨 MCP 闆嗘垚銆?
- **L2 native capture锛?*宸ュ叿鍘熺敓浜嬩欢鎹曡幏锛涙病鏈夋槑纭瘉鎹椂鍙睘浜?roadmap銆?

涓嶈兘鎶?L0/L1 鏂囨。璺緞瀹ｄ紶鎴愬畬鏁村師鐢熸崟鑾枫€?

## 寮€鍙戜笌楠岃瘉

```powershell
python -m pip install -e .
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
python -m pytest -q
```

褰撳墠 Windows 鏈湴楠岃瘉鍩虹嚎涓?50 椤规祴璇曢€氳繃銆侴itHub Actions 宸插畾涔?Windows/Ubuntu銆丳ython 3.10鈥?.12銆乪ditable/wheel 鐭╅樀銆?

## 璁稿彲璇?

棰勬湡妯″紡鏄?**source-available锛堟簮鐮佸彲瑙侊級**锛屼笉鏄?OSI open source銆?

MycEvo 浣跨敤 [Apache-2.0](LICENSE) 璁稿彲璇併€傜涓夋柟渚濊禆鍜岀礌鏉愪粛閫傜敤鍏跺師濮嬭鍙瘉锛岃瑙?[NOTICE](NOTICE) 鍜?[绗笁鏂瑰０鏄嶿(THIRD_PARTY_NOTICES.md)銆?

褰撳墠涓嶈璁哄叿浣撲环鏍笺€傚弬瑙侊細

- [璁稿彲璇?FAQ](LICENSING_FAQ.md)
- [鍟嗕笟鎺堟潈璇存槑](COMMERCIAL-LICENSE.md)
- [璁稿彲璇佹潵婧愪笌瀹℃壒闂ㄧ](docs/release/license-provenance.md)

## 璐＄尞

Technical Preview 闃舵娆㈣繋 Issue銆佽璁″弽棣堛€佸鐜版姤鍛娿€乤dapter proposal 鍜岄潪瀹炶川鏂囨。淇銆傝础鐚€呮巿鏉冩祦绋嬫壒鍑嗗墠锛屼笉鍚堝苟瀹炶川浠ｇ爜 PR銆傝瑙?[CONTRIBUTING.md](CONTRIBUTING.md)銆?

## 瀹夊叏涓庨殣绉?

涓嶈鎻愪氦绉佹湁 prompt銆佷换鍔?trace銆佸嚟鎹€佹湭鍏紑鐮旂┒銆佸師濮嬫暟鎹€佹暟鎹簱鎴栫敤鎴风粷瀵硅矾寰勩€傚弬瑙?[SECURITY.md](SECURITY.md) 鍜孾鍏紑鏂囦欢娓呭崟](docs/release/public-file-manifest.yaml)銆?
