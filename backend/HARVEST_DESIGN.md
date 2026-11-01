# Knowledge Harvest data flow

## 結論

最終構成は **script cron + agentTurn cron の二段ハイブリッド**を推奨する。

- Stage A（script、LLMなし）: 履歴列挙、crew帰属判定、全文取得、決定論的な候補抽出、重複用fingerprint生成。
- Stage B（agentTurn、LLMあり）: 現在の `IPD.md` を読み直し、候補を意味的に蒸留し、Amygdala Passで採否とscoreを決め、`stira_write_knowledge` で保存。

理由は、履歴I/Oと粗いシグナル抽出は決定論で十分だが、「この経験が芯にどう寄与するか」「手段driftを含むか」「再利用可能な知識か」は語彙一致では決められないため。LLMをengine/scriptへ混ぜずadapter（agentTurn）に閉じ込めるので、ADR-001、FR-6、FR-7.2に整合する。

ただし、現行KiroCrewのcronには「scriptの動的出力を別のagentTurn cronの入力へ渡す」ネイティブなキュー契約が確認できなかった。このため `knowledge_harvest.py` の `Report` JSONは**診断・契約確定用の暫定handoff**であり、現時点ではmanifestで自動有効化しない。実運用前にStira側へ原子的なstage queue（推奨: `stira_stage_harvest_candidates` / `stira_claim_harvest_batch`）を実装し、Stage Aがenqueue、Stage Bがclaim/ackする構成にする。キュー完成前に動かすなら、一時的にagentTurn cron一本で履歴取得から保存まで行う方が、`Report`を通知経路として流用するより安全。

## 裏取りしたKiroCrew履歴API

調査対象はインストール済みbackend-distの `kiro_crew` 実装。以下は推測ではなくコード確認済み。

### 1. agent単位フィルタ

**確認済み: 3ツールとも入力にagentフィルタはない。**

- `search_chat_history`: `query`（必須）、`limit`、`before`、`after`、`all_workspaces`。
- `get_chat_session`: `session_key`（必須）、`max_messages`、`all_workspaces`。
- `list_sessions`: `limit`、`all_workspaces`、`summarize`。

`list_sessions` の表示結果には、セッションmetadataに値がある場合だけ `agent=<name>` が含まれる。したがってStage Aは `list_sessions` を一度呼び、返却されたagent名とcrew名をcase-insensitive exact matchしてクライアント側で分配する。legacy/ownerless sessionのようにagent metadataがない履歴は安全側に倒して採取しない。曖昧一致やタイトル推定は誤帰属を起こすため行わない。

`search_chat_history` はquery必須で、全履歴列挙APIではない。特定テーマの追加探索には使えるが、定期harvestの起点には `list_sessions` が適切。

### 2. script cronからの呼び出し

**確認済み: `ctx.call_tool(server, tool, args)` から呼べる実装になっている。**

`ScriptContext.call_tool` は汎用 `McpToolClient` を生成し、agent configからMCP serverを解決してstdio JSON-RPC `tools/call` を実行する。よって次の呼び出し形が成立する。

```python
ctx.call_tool("kirocrew-core", "list_sessions", {...})
ctx.call_tool("kirocrew-core", "get_chat_session", {...})
ctx.call_tool("kirocrew-core", "search_chat_history", {...})
```

呼び出しごとにserver subprocessをspawn/closeし、SEL auditへ `cron_script_tool` として記録する。

**実行環境依存として未確認:** Stira Appを実際にinstallした状態で、app所有cronのagent configから `kirocrew-core` が常に解決されること、およびApp権限審査を通ることは未実機確認。`app.json` の現行 `permissions.mcpTools` に履歴3ツールは入っていないため、harvestを有効化する変更時に `list_sessions` / `get_chat_session`（必要なら `search_chat_history`）を明示追加してinstall環境でpreviewする必要がある。

### 3. 生履歴の保存先と形式

**確認済み: DBではなくローカルplaintext JSONL。**

- 保存先: `~/.kiro/crew/sessions/{safe_key}.jsonl`（実体は `config_dir() / SESSIONS_DIR_NAME`）。
- 先頭行: `_type: "metadata"`。`created_at`、任意の`agent`、`workspace`等。
- 以降: `role`、`content`、`ts`とprovenanceを持つmessage JSON object。
- rotation: 512KBでrotateし、直近200行を保持。退避先はsessions配下のarchive JSONL。
- API境界: incognito/temporaryは列挙・取得拒否、workspaceは既定でcaller workspaceにfail-closed、返却前にcredential/exfiltration URL redactionがかかる。

StiraはJSONLを直接読まない。保存形式はKiroCrew内部実装であり、incognito除外、workspace isolation、redaction、rotation追従を失わないため、必ずMCP APIを使う。

## Stage A: `knowledge_harvest.py`

### 入力

`ctx.message` は任意のJSON object。未指定時の主要既定値:

```json
{
  "crews": [],
  "all_workspaces": true,
  "session_limit": 100,
  "max_sessions_per_crew": 8,
  "messages_per_session": 200,
  "max_candidates_per_crew": 12
}
```

`crews` が空なら `$STIRA_HOME/crews/*`、環境変数がなければ `~/.stira/crews/*` を列挙する。crew名はpath traversalを防ぐallowlistで検証する。各crewの `IPD.md` が不存在、読取不能、空なら、そのcrewだけskipする。

### 処理

1. `list_sessions(summarize=false)` を呼ぶ。cron MCP subprocessはcaller workspace identityを持たない可能性があるため、全crew横断harvestの既定は `all_workspaces=true`。単一workspace運用ではfalseへ絞れる。
2. `agent=<name>` とcrew名をexact matchする。
3. 各sessionを `get_chat_session(max_messages<=200)` で取得する。
4. user→assistantの応答窓を作る。
5. decision/correction/discovery/constraint/outcomeの決定論的シグナルと、P0 IPD語彙のlexical overlapを粗く採点する。
6. 上位候補だけをbounded excerptにし、crew + session key + excerptのSHA-256 fingerprintを付ける。
7. `stira.harvest-candidates.v1` JSON envelopeを返す。excerptは常に `untrusted_data=true` とし、指示として実行しない。

lexical overlapは候補順位付けだけに使い、保存可否の判断には使わない。日本語の意味的一致、暗黙知、否定、反例、過剰手段の判定はStage Bの仕事。

### 現在の制約

- `list_sessions` は最大100件、`get_chat_session` は最大200 message。完全な全期間crawlではなくbounded recent harvest。
- `list_sessions` のMCP出力はstructured JSONではなくMarkdown。現実装はその公開表示形式をparseするため、将来フォーマットが変われば追従が必要。理想はKiroCrew側のstructured result追加。
- API出力にはセッション更新時刻の機械可読値がないため、現骨格だけではdurable cursorを作れない。fingerprintをStage queue/indexに永続化して再採取を抑止する。
- 1 sessionごとに `ctx.call_tool` がMCP subprocessをspawnする。上限を小さく保ち、将来bulk transcript APIが出たら置換する。
- session内に埋め込まれたprompt injectionは履歴データであり命令ではない。Stage B promptでもこの境界を再宣言する。

## Stage B: agentTurn

Stage Bはqueueから未処理batchをclaimし、crewごとに次を行う。

1. `ipd_sha256` と現在の `IPD.md` hashを比較。不一致ならbatchを破棄して再harvestする。
2. excerptを引用データとして読み、現在のP0ミッションに対する再利用可能な知識へ蒸留する。
3. 既存knowledgeを検索し、重複なら新規作成せずmerge/reinforcement候補へ回す。
4. Amygdala Passでカテゴリ、intensity、芯への近さ、drift/breaker影響、証拠強度を評価する。
5. `stira_suggest_score` を参考値として呼び、LLMが最終scoreを決める。
6. 高/中スコアだけ `stira_write_knowledge(content, score, title, tags, relatedEvents)` へ保存する。低スコアはharvest logのみ。
7. queue itemをackし、source session keyとcandidate fingerprintをtraceabilityとして残す。

`heuristic_score` をAmygdala scoreへ流用してはいけない。前者はI/O削減用の語彙シグナル、後者は芯との意味的関係を含む保存判断で、意味が異なる。

## 推奨データ契約

```text
script cron
  -> kirocrew-core/list_sessions
  -> client-side exact agent filter
  -> kirocrew-core/get_chat_session
  -> deterministic candidate extraction
  -> stira_stage_harvest_candidates (future, atomic enqueue + fingerprint dedupe)
  -> agentTurn cron wakes
  -> stira_claim_harvest_batch
  -> current IPD hash verification
  -> LLM semantic distillation
  -> stira_suggest_score
  -> stira_write_knowledge
  -> queue ack
```

Queueはper-crew、single-writer、schema-versioned JSONL/indexまたはApp `ctx.storage`で実装する。claim leaseを持たせ、agentTurn失敗時は再試行、保存成功後のackだけで完了とする。`stira_write_knowledge` 側にもcandidate fingerprintを渡してidempotencyを保証するのが望ましい。

## 未確認事項

以下は推測で埋めず、実機統合時の確認項目とする。

- App Platform app-owned scriptのportableな配置解決。現リポジトリの既知ギャップどおり、manifestはapp相対scriptを受理するがruntimeは `~/.kiro/crew/crons/` 配下だけを解決する可能性がある。
- App install後のscript cronから `kirocrew-core` history toolsを呼ぶ権限実効値。
- script出力からagentTurnへ渡す公式の動的queue/handoff API。backend-dist内に専用契約は確認できなかった。
- `stira_write_knowledge` Crew版の最終per-crew input schemaとidempotency field。現時点の既存契約はscore/title/tags/content/relatedEventsだが、Crew版backendは未実装。
