import { useAppApi as e } from "@kirocrew/app-sdk";
import { Card as t, CardTitle as n, PageHeader as r, StatCard as i } from "@kirocrew/app-sdk/ui";
import { useCallback as a, useEffect as o, useMemo as s, useRef as c, useState as l } from "react";
import { Fragment as u, jsx as d, jsxs as f } from "react/jsx-runtime";
//#region src/HarvestPanel.tsx
var p = 3500, m = 6e5, h = /* @__PURE__ */ new Set([
	"completed",
	"failed",
	"returned",
	"start_failed"
]), g = [
	"started",
	"collected",
	"distilled",
	"completed"
];
function _(e) {
	if (!e) return !1;
	let t = e.status?.toLowerCase();
	if (t && h.has(t)) return !1;
	if (e.requestedAt) {
		let t = Date.parse(e.requestedAt);
		if (Number.isFinite(t) && Date.now() - t >= m) return !1;
	}
	return !0;
}
function v(e) {
	return typeof e == "number" ? e : 0;
}
function y({ phase: e, active: t }) {
	let n = e === "failed" ? void 0 : e, r = n ? g.indexOf(n) : -1;
	return /* @__PURE__ */ f("div", {
		className: "stira-harvest-progress",
		"aria-label": "Harvest progress",
		children: [g.map((e, n) => /* @__PURE__ */ d("span", {
			className: `stira-harvest-step stira-harvest-step--${n < r || !t && n === r ? "done" : n === r ? "current" : "pending"}`,
			children: e
		}, e)), e === "failed" && /* @__PURE__ */ d("span", {
			className: "stira-harvest-step stira-harvest-step--failed",
			children: "failed"
		})]
	});
}
function b({ run: e, current: t, active: n }) {
	let r = e.counts ?? {}, i = e.writes ?? {}, a = e.layerCounts ?? {}, o = n || e.phase === "completed" || e.phase === "failed" ? e.phase : `interrupted: ${e.phase}`, s = /* @__PURE__ */ f("div", {
		className: "stira-harvest-run-body",
		children: [
			/* @__PURE__ */ f("div", {
				className: "stira-harvest-diagnostics",
				children: [/* @__PURE__ */ f("span", { children: [/* @__PURE__ */ d("strong", { children: "stiraHome" }), /* @__PURE__ */ d("code", { children: e.stiraHome ?? "not reported" })] }), /* @__PURE__ */ f("span", { children: [/* @__PURE__ */ d("strong", { children: "crewsDiscovered" }), e.crewsDiscovered ?? "not reported"] })]
			}),
			/* @__PURE__ */ f("div", {
				className: "stira-harvest-run-grid",
				children: [
					/* @__PURE__ */ f("span", { children: [
						/* @__PURE__ */ d("strong", { children: "拾った" }),
						v(r.sessionsMatched),
						"/",
						v(r.sessionsListed),
						" sessions · ",
						v(r.cronRunsMatched),
						" cron · ",
						v(r.subagentsMatched),
						" subagent"
					] }),
					/* @__PURE__ */ f("span", { children: [
						/* @__PURE__ */ d("strong", { children: "通した" }),
						v(r.candidates),
						" → ",
						v(r.distilled),
						" → ",
						v(r.accepted)
					] }),
					/* @__PURE__ */ f("span", { children: [
						/* @__PURE__ */ d("strong", { children: "層" }),
						v(a.core),
						" core · ",
						v(a.knowledge),
						" knowledge · ",
						v(a.log),
						" log"
					] }),
					/* @__PURE__ */ f("span", { children: [
						/* @__PURE__ */ d("strong", { children: "書いた" }),
						v(i.written),
						" · fingerprint重複 ",
						v(i.duplicateFingerprint),
						" · semantic重複 ",
						v(i.duplicateSemantic),
						" · 失敗 ",
						v(i.failed)
					] })
				]
			}),
			(e.skips?.length ?? 0) > 0 && /* @__PURE__ */ f("div", {
				className: "stira-harvest-skips",
				children: [/* @__PURE__ */ d("strong", { children: "skip理由" }), e.skips.map((e, t) => /* @__PURE__ */ f("span", { children: [
					e.reason,
					" × ",
					e.count,
					e.detail ? ` — ${e.detail}` : ""
				] }, `${e.reason}-${t}`))]
			}),
			/* @__PURE__ */ f("div", {
				className: "stira-harvest-agents",
				children: [/* @__PURE__ */ d("strong", { children: "agentsSeen" }), e.agentsSeen?.length ? e.agentsSeen.map((e) => `${e.agent} × ${e.count}`).join(", ") : "none reported"]
			}),
			(e.errors?.length ?? 0) > 0 && /* @__PURE__ */ d("div", {
				className: "stira-harvest-run-errors",
				role: "alert",
				children: e.errors.map((e, t) => /* @__PURE__ */ f("span", { children: [
					e.stage ?? "unknown",
					" / ",
					e.type ?? "error",
					": ",
					e.message ?? "no detail"
				] }, t))
			})
		]
	}), c = /* @__PURE__ */ f(u, { children: [
		/* @__PURE__ */ d("span", {
			className: `stira-harvest-phase stira-harvest-phase--${e.phase}`,
			children: o
		}),
		/* @__PURE__ */ d("code", { children: e.runId }),
		/* @__PURE__ */ f("small", { children: [e.finishedAt ?? e.startedAt ?? "time not reported", e.model ? ` · ${e.model}` : ""] })
	] });
	return t ? /* @__PURE__ */ f("section", {
		className: "stira-harvest-current",
		children: [/* @__PURE__ */ d("header", { children: c }), s]
	}) : /* @__PURE__ */ f("details", {
		className: "stira-harvest-history",
		children: [/* @__PURE__ */ d("summary", { children: c }), s]
	});
}
function x({ runs: e, dispatch: t, pollingRequested: n, error: r, onRefresh: i }) {
	let a = c(i);
	a.current = i;
	let s = _(t), l = n || s, u = ((t?.runId ? e.find((e) => e.runId === t.runId) : void 0) ?? e[0])?.phase ?? (s ? "started" : void 0);
	return o(() => {
		let e = !0, t = () => {
			e && a.current();
		};
		if (t(), !l) return () => {
			e = !1;
		};
		let n = window.setInterval(t, p);
		return () => {
			e = !1, window.clearInterval(n);
		};
	}, [l]), /* @__PURE__ */ f("section", {
		className: "stira-harvest-panel",
		"aria-label": "Harvest observability",
		children: [
			/* @__PURE__ */ d("style", { children: S }),
			/* @__PURE__ */ f("div", {
				className: "stira-harvest-panel-heading",
				children: [/* @__PURE__ */ d("strong", { children: "直近の収穫" }), /* @__PURE__ */ d("button", {
					type: "button",
					onClick: () => void i(),
					disabled: s,
					children: s ? "収穫中…" : "ログ更新"
				})]
			}),
			/* @__PURE__ */ d(y, {
				phase: u,
				active: s
			}),
			s && /* @__PURE__ */ f("p", {
				className: "stira-harvest-live",
				role: "status",
				children: ["収穫中… ", u ?? t?.status ?? "started"]
			}),
			r && /* @__PURE__ */ f("p", {
				className: "stira-harvest-log-error",
				role: "alert",
				children: ["Harvest log unavailable: ", r]
			}),
			!r && e.length === 0 && /* @__PURE__ */ f("p", {
				className: "stira-harvest-log-empty",
				children: ["実行ログなし", t?.status ? ` · dispatch ${t.status}` : ""]
			}),
			e.map((e, n) => /* @__PURE__ */ d(b, {
				run: e,
				current: n === 0,
				active: s && e.runId === t?.runId
			}, e.runId))
		]
	});
}
var S = "\n.stira-harvest-panel { margin-top: .8rem; padding-top: .75rem; border-top: 1px solid var(--border, #353a42); font-size: .7rem; color: var(--muted, #8f98a4); }\n.stira-harvest-panel-heading { display: flex; align-items: center; justify-content: space-between; gap: .5rem; color: var(--text, #d9dde3); }\n.stira-harvest-panel-heading button { border: 1px solid var(--border, #353a42); border-radius: .4rem; padding: .25rem .45rem; background: transparent; color: var(--muted, #8f98a4); cursor: pointer; }\n.stira-harvest-panel-heading button:disabled { opacity: .55; cursor: wait; }\n.stira-harvest-progress { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: .3rem; margin-top: .55rem; }\n.stira-harvest-step { position: relative; padding-top: .45rem; color: var(--muted, #8f98a4); text-align: center; }\n.stira-harvest-step::before { content: ''; position: absolute; inset: 0 0 auto; height: 2px; border-radius: 999px; background: var(--border, #353a42); }\n.stira-harvest-step--done, .stira-harvest-step--current { color: var(--text, #d9dde3); }\n.stira-harvest-step--done::before { background: var(--ok, #6ecf8d); }\n.stira-harvest-step--current::before { background: var(--accent, #79a676); box-shadow: 0 0 .4rem color-mix(in srgb, var(--accent, #79a676) 55%, transparent); }\n.stira-harvest-step--failed { color: var(--danger, #ef6b73); }\n.stira-harvest-step--failed::before { background: var(--danger, #ef6b73); }\n.stira-harvest-live { margin: .45rem 0 0; color: var(--accent, #79a676); }\n.stira-harvest-current, .stira-harvest-history { margin-top: .55rem; border: 1px solid var(--border, #353a42); border-radius: .5rem; padding: .55rem; background: color-mix(in srgb, var(--card, #171a1f) 82%, var(--bg, #0d0f12)); }\n.stira-harvest-current header, .stira-harvest-history summary { display: flex; flex-wrap: wrap; align-items: center; gap: .4rem; cursor: default; }\n.stira-harvest-history summary { cursor: pointer; }\n.stira-harvest-current code, .stira-harvest-history code, .stira-harvest-diagnostics code { overflow-wrap: anywhere; color: var(--text, #d9dde3); }\n.stira-harvest-current small, .stira-harvest-history small { margin-left: auto; }\n.stira-harvest-phase { border: 1px solid var(--border, #353a42); border-radius: 999px; padding: .1rem .35rem; }\n.stira-harvest-phase--completed { color: var(--ok, #6ecf8d); border-color: color-mix(in srgb, var(--ok, #6ecf8d) 50%, transparent); }\n.stira-harvest-phase--failed { color: var(--danger, #ef6b73); border-color: color-mix(in srgb, var(--danger, #ef6b73) 50%, transparent); }\n.stira-harvest-run-body { display: grid; gap: .5rem; margin-top: .5rem; }\n.stira-harvest-diagnostics { display: grid; gap: .25rem; padding: .4rem; border-radius: .35rem; background: color-mix(in srgb, var(--warning, #e4a853) 7%, transparent); }\n.stira-harvest-diagnostics span, .stira-harvest-run-grid span { display: grid; gap: .1rem; }\n.stira-harvest-run-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: .4rem; }\n.stira-harvest-skips, .stira-harvest-run-errors { display: grid; gap: .15rem; }\n.stira-harvest-run-errors { padding: .4rem; border: 1px solid color-mix(in srgb, var(--danger, #ef6b73) 45%, transparent); border-radius: .35rem; color: var(--danger, #ef6b73); }\n.stira-harvest-log-error { color: var(--danger, #ef6b73); }\n.stira-harvest-log-empty { margin: .5rem 0 0; }\n@media (max-width: 560px) { .stira-harvest-run-grid { grid-template-columns: 1fr; } }\n", C = "/api/apps/stira", w = [
	{
		value: 300,
		label: "5分"
	},
	{
		value: 900,
		label: "15分"
	},
	{
		value: 3600,
		label: "1時間"
	},
	{
		value: 21600,
		label: "6時間"
	},
	{
		value: 86400,
		label: "1日"
	},
	{
		value: 604800,
		label: "1週間"
	}
];
function T(e) {
	if (!e) return "停止中";
	let t = new Date(e);
	return Number.isNaN(t.getTime()) ? "次回時刻不明" : `次回 ${t.toLocaleString()}`;
}
function ee() {
	let r = e(), [i, s] = l(null), [c, u] = l({}), [p, m] = l(null), h = a(async () => {
		try {
			let e = await r.get(`${C}/schedule`);
			s(e), m(null);
		} catch (e) {
			m(e instanceof Error ? e.message : "スケジュールを取得できませんでした");
		}
	}, [r]);
	o(() => {
		h();
	}, [h]);
	let g = a(async (e, t) => {
		u((t) => ({
			...t,
			[e]: !0
		}));
		try {
			let n = await r.post(`${C}/schedule/${encodeURIComponent(e)}`, t);
			s((t) => t && {
				coreDefined: n.coreDefined,
				crons: t.crons.map((t) => t.name === e ? n.cron : t)
			}), m(null);
		} catch (e) {
			m(e instanceof Error ? e.message : "スケジュールを更新できませんでした"), await h();
		} finally {
			u((t) => ({
				...t,
				[e]: !1
			}));
		}
	}, [r, h]);
	return /* @__PURE__ */ f("section", {
		className: "stira-schedule",
		"aria-labelledby": "stira-schedule-title",
		children: [/* @__PURE__ */ d("style", { children: E }), /* @__PURE__ */ f(t, { children: [
			/* @__PURE__ */ f("div", {
				className: "stira-schedule-heading",
				children: [/* @__PURE__ */ f("div", { children: [/* @__PURE__ */ d(n, { children: /* @__PURE__ */ d("span", {
					id: "stira-schedule-title",
					children: "スケジュール"
				}) }), /* @__PURE__ */ d("p", { children: "Stiraの自動実行をON/OFFし、実行間隔を変更する。" })] }), /* @__PURE__ */ d("button", {
					type: "button",
					onClick: () => void h(),
					children: "再読込"
				})]
			}),
			p && /* @__PURE__ */ d("p", {
				className: "stira-schedule-error",
				role: "alert",
				children: p
			}),
			!i && !p && /* @__PURE__ */ d("p", {
				className: "stira-schedule-muted",
				children: "スケジュールを読込中…"
			}),
			i && !i.coreDefined && /* @__PURE__ */ d("p", {
				className: "stira-schedule-guard",
				children: "芯が未定義のため、drift・慣れ相殺・収穫は有効化できない。"
			}),
			/* @__PURE__ */ d("div", {
				className: "stira-schedule-list",
				children: i?.crons.map((e) => {
					let t = c[e.name] === !0, n = e.guarded && !e.enabled, r = w.some((t) => t.value === e.intervalSeconds);
					return /* @__PURE__ */ f("div", {
						className: "stira-schedule-row",
						children: [
							/* @__PURE__ */ f("div", {
								className: "stira-schedule-identity",
								children: [/* @__PURE__ */ d("strong", { children: e.label }), /* @__PURE__ */ d("span", { children: e.available ? T(e.nextRunAt) : "未登録" })]
							}),
							/* @__PURE__ */ f("label", {
								className: "stira-schedule-toggle",
								children: [/* @__PURE__ */ d("input", {
									type: "checkbox",
									checked: e.enabled,
									disabled: t || !e.available || n,
									onChange: (t) => void g(e.name, { enabled: t.target.checked })
								}), /* @__PURE__ */ d("span", { children: e.enabled ? "ON" : "OFF" })]
							}),
							/* @__PURE__ */ f("label", {
								className: "stira-schedule-interval",
								children: [/* @__PURE__ */ d("span", { children: "間隔" }), /* @__PURE__ */ f("select", {
									value: e.intervalSeconds,
									disabled: t || !e.available,
									onChange: (t) => void g(e.name, { intervalSeconds: Number(t.target.value) }),
									children: [!r && /* @__PURE__ */ f("option", {
										value: e.intervalSeconds,
										children: [e.intervalSeconds, "秒"]
									}), w.map((e) => /* @__PURE__ */ d("option", {
										value: e.value,
										children: e.label
									}, e.value))]
								})]
							})
						]
					}, e.name);
				})
			})
		] })]
	});
}
var E = "\n.stira-schedule { margin: 0 0 1rem; }\n.stira-schedule-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 1rem; }\n.stira-schedule-heading p { margin: .2rem 0 0; color: var(--muted, #8f98a4); font-size: .76rem; }\n.stira-schedule-heading button { border: 1px solid var(--border, #353a42); border-radius: .5rem; padding: .42rem .68rem; background: var(--bg-elevated, #22262d); color: var(--text, #d9dde3); cursor: pointer; }\n.stira-schedule-list { display: grid; gap: .45rem; margin-top: .85rem; }\n.stira-schedule-row { display: grid; grid-template-columns: minmax(10rem, 1fr) auto minmax(8rem, auto); align-items: center; gap: .85rem; padding: .65rem .75rem; border: 1px solid var(--border, #353a42); border-radius: .6rem; background: color-mix(in srgb, var(--bg-elevated, #22262d) 55%, transparent); }\n.stira-schedule-identity { display: flex; flex-direction: column; gap: .14rem; }\n.stira-schedule-identity strong { color: var(--text-strong, #fff); font-size: .82rem; }\n.stira-schedule-identity span, .stira-schedule-interval > span { color: var(--muted, #8f98a4); font-size: .68rem; }\n.stira-schedule-toggle, .stira-schedule-interval { display: flex; align-items: center; gap: .42rem; }\n.stira-schedule-toggle { min-width: 3.8rem; color: var(--muted, #8f98a4); font-size: .7rem; font-weight: 700; }\n.stira-schedule-toggle input { width: 1.05rem; height: 1.05rem; accent-color: var(--accent, #79a676); }\n.stira-schedule-interval select { border: 1px solid var(--border, #353a42); border-radius: .45rem; padding: .35rem .48rem; background: var(--bg, #0d0f12); color: var(--text, #d9dde3); }\n.stira-schedule-toggle input:disabled, .stira-schedule-interval select:disabled { cursor: not-allowed; opacity: .55; }\n.stira-schedule-error, .stira-schedule-guard { margin: .7rem 0 0; padding: .55rem .7rem; border-radius: .5rem; font-size: .74rem; }\n.stira-schedule-error { color: var(--danger, #ef6b73); background: color-mix(in srgb, var(--danger, #ef6b73) 9%, transparent); }\n.stira-schedule-guard { color: var(--warning, #e4a853); background: color-mix(in srgb, var(--warning, #e4a853) 9%, transparent); }\n.stira-schedule-muted { color: var(--muted, #8f98a4); font-size: .75rem; }\n@media (max-width: 680px) {\n  .stira-schedule-row { grid-template-columns: 1fr auto; }\n  .stira-schedule-interval { grid-column: 1 / -1; justify-content: space-between; }\n}\n";
//#endregion
//#region src/LeafDetail.tsx
function D(e, t) {
	let n = e.toLocaleLowerCase().normalize("NFKC").replace(/[^\p{L}\p{N}\s-]/gu, "").trim().replace(/[\s-]+/g, "-") || "section", r = t.get(n) ?? 0;
	return t.set(n, r + 1), r === 0 ? n : `${n}-${r + 1}`;
}
function O(e) {
	return /^(#{1,6})\s+/.test(e) || /^```/.test(e) || /^\s*(?:[-+*]|\d+\.)\s+/.test(e) || /^>\s?/.test(e) || /^\s*(?:---+|___+|\*\*\*+)\s*$/.test(e);
}
function k(e) {
	let t = e.replace(/\r\n?/g, "\n").split("\n"), n = [], r = [], i = /* @__PURE__ */ new Map();
	for (let e = 0; e < t.length;) {
		let a = t[e];
		if (!a.trim()) {
			e += 1;
			continue;
		}
		let o = /^(#{1,6})\s+(.+?)\s*#*\s*$/.exec(a);
		if (o) {
			let t = o[1].length, a = o[2], s = D(a, i);
			r.push({
				depth: t,
				text: a,
				id: s
			}), n.push({
				kind: "heading",
				depth: t,
				text: a,
				id: s
			}), e += 1;
			continue;
		}
		let s = /^```\s*([^\s`]*)\s*$/.exec(a);
		if (s) {
			let r = [];
			for (e += 1; e < t.length && !/^```\s*$/.test(t[e]);) r.push(t[e]), e += 1;
			e < t.length && (e += 1), n.push({
				kind: "code",
				language: s[1],
				text: r.join("\n")
			});
			continue;
		}
		if (/^\s*(?:---+|___+|\*\*\*+)\s*$/.test(a)) {
			n.push({ kind: "rule" }), e += 1;
			continue;
		}
		let c = /^\s*(?:(\d+)\.|[-+*])\s+(.+)$/.exec(a);
		if (c) {
			let r = c[1] !== void 0, i = [];
			for (; e < t.length;) {
				let n = /^\s*(?:(\d+)\.|[-+*])\s+(.+)$/.exec(t[e]);
				if (!n || n[1] !== void 0 !== r) break;
				i.push(n[2]), e += 1;
			}
			n.push({
				kind: "list",
				ordered: r,
				items: i
			});
			continue;
		}
		if (/^>\s?/.test(a)) {
			let r = [];
			for (; e < t.length && /^>\s?/.test(t[e]);) r.push(t[e].replace(/^>\s?/, "")), e += 1;
			n.push({
				kind: "quote",
				text: r.join("\n")
			});
			continue;
		}
		let l = [a.trim()];
		for (e += 1; e < t.length && t[e].trim() && !O(t[e]);) l.push(t[e].trim()), e += 1;
		n.push({
			kind: "paragraph",
			text: l.join(" ")
		});
	}
	return {
		blocks: n,
		headings: r
	};
}
function A(e) {
	try {
		let t = new URL(e);
		return t.protocol === "https:" || t.protocol === "http:" ? t.href : null;
	} catch {
		return null;
	}
}
function j(e) {
	let t = /(`[^`]+`|\*\*[^*]+\*\*|\*[^*]+\*|\[[^\]]+\]\([^\s)]+\))/g, n = [], r = 0;
	for (let i of e.matchAll(t)) {
		let t = i.index ?? 0;
		t > r && n.push(e.slice(r, t));
		let a = i[0];
		if (a.startsWith("`")) n.push(/* @__PURE__ */ d("code", { children: a.slice(1, -1) }, t));
		else if (a.startsWith("**")) n.push(/* @__PURE__ */ d("strong", { children: a.slice(2, -2) }, t));
		else if (a.startsWith("*")) n.push(/* @__PURE__ */ d("em", { children: a.slice(1, -1) }, t));
		else {
			let e = /^\[([^\]]+)\]\(([^)]+)\)$/.exec(a), r = e ? A(e[2]) : null;
			n.push(r ? /* @__PURE__ */ d("a", {
				href: r,
				target: "_blank",
				rel: "noreferrer",
				children: e?.[1]
			}, t) : a);
		}
		r = t + a.length;
	}
	return r < e.length && n.push(e.slice(r)), n;
}
function M({ content: e }) {
	let t = s(() => k(e), [e]);
	return /* @__PURE__ */ f(u, { children: [t.headings.length > 0 && /* @__PURE__ */ f("nav", {
		className: "stira-leaf-toc",
		"aria-label": "本文のセクション",
		children: [/* @__PURE__ */ d("strong", { children: "Sections" }), /* @__PURE__ */ d("ol", { children: t.headings.map((e) => /* @__PURE__ */ d("li", {
			style: { paddingInlineStart: `${(e.depth - 1) * .65}rem` },
			children: /* @__PURE__ */ d("a", {
				href: `#${e.id}`,
				children: e.text
			})
		}, e.id)) })]
	}), /* @__PURE__ */ d("div", {
		className: "stira-markdown",
		children: t.blocks.map((e, t) => {
			if (e.kind === "heading") {
				let t = `h${e.depth}`;
				return /* @__PURE__ */ d(t, {
					id: e.id,
					children: j(e.text)
				}, e.id);
			}
			if (e.kind === "paragraph") return /* @__PURE__ */ d("p", { children: j(e.text) }, t);
			if (e.kind === "code") return /* @__PURE__ */ d("pre", { children: /* @__PURE__ */ d("code", {
				"data-language": e.language,
				children: e.text
			}) }, t);
			if (e.kind === "quote") return /* @__PURE__ */ d("blockquote", { children: j(e.text) }, t);
			if (e.kind === "rule") return /* @__PURE__ */ d("hr", {}, t);
			let n = e.ordered ? "ol" : "ul";
			return /* @__PURE__ */ d(n, { children: e.items.map((e, t) => /* @__PURE__ */ d("li", { children: j(e) }, t)) }, t);
		})
	})] });
}
function te({ crew: e, leaf: t, onPrune: n, onUnprune: r, onClose: i }) {
	let a = c(null), [s, u] = l(!1), [p, m] = l(!1), [h, g] = l(null), [_, v] = l(!1), [y, b] = l(null);
	o(() => {
		a.current?.focus();
		let e = (e) => {
			if (e.key === "Escape") {
				if (s && !p) {
					u(!1), g(null);
					return;
				}
				p || i();
			}
		};
		return window.addEventListener("keydown", e), () => window.removeEventListener("keydown", e);
	}, [
		s,
		i,
		p
	]), o(() => {
		u(!1), m(!1), g(null), v(!1), b(null);
	}, [t.id]);
	let x = async () => {
		if (n) {
			m(!0), g(null);
			try {
				await n(), i();
			} catch (e) {
				g(e instanceof Error ? e.message : "剪定に失敗した");
			} finally {
				m(!1);
			}
		}
	}, S = async () => {
		if (r) {
			v(!0), b(null);
			try {
				await r(), i();
			} catch (e) {
				b(e instanceof Error ? e.message : "葉を戻せなかった");
			} finally {
				v(!1);
			}
		}
	};
	return /* @__PURE__ */ f("div", {
		className: "stira-leaf-overlay",
		role: "presentation",
		onMouseDown: i,
		children: [/* @__PURE__ */ d("style", { children: N }), /* @__PURE__ */ f("aside", {
			className: "stira-leaf-detail",
			role: "dialog",
			"aria-modal": "true",
			"aria-labelledby": "stira-leaf-title",
			onMouseDown: (e) => e.stopPropagation(),
			onKeyDown: (e) => e.stopPropagation(),
			children: [
				/* @__PURE__ */ f("header", {
					className: "stira-leaf-detail-header",
					children: [/* @__PURE__ */ f("div", { children: [/* @__PURE__ */ f("p", {
						className: "stira-leaf-eyebrow",
						children: [
							e,
							" / ",
							t.domain
						]
					}), /* @__PURE__ */ d("h2", {
						id: "stira-leaf-title",
						children: t.title
					})] }), /* @__PURE__ */ d("button", {
						ref: a,
						type: "button",
						"aria-label": "Close knowledge detail",
						onClick: i,
						children: "×"
					})]
				}),
				/* @__PURE__ */ d("p", {
					className: "stira-leaf-summary",
					children: t.summary || "No summary."
				}),
				/* @__PURE__ */ f("div", {
					className: "stira-leaf-meta",
					"aria-label": "Knowledge metadata",
					children: [
						/* @__PURE__ */ d("span", { children: t.layer }),
						/* @__PURE__ */ f("span", { children: [
							"score ",
							Math.round(t.score * 100),
							"%"
						] }),
						/* @__PURE__ */ f("span", { children: [
							"effective ",
							Math.round(t.effectiveScore * 100),
							"%"
						] }),
						t.tags.map((e) => /* @__PURE__ */ f("span", { children: ["#", e] }, e))
					]
				}),
				/* @__PURE__ */ d("div", {
					className: "stira-leaf-body",
					children: /* @__PURE__ */ d(M, { content: t.content || "_No content available._" })
				}),
				(n || r) && /* @__PURE__ */ d("footer", {
					className: "stira-leaf-actions",
					children: r ? /* @__PURE__ */ f("div", {
						className: "stira-restore-action",
						children: [y && /* @__PURE__ */ d("p", {
							className: "stira-prune-error",
							role: "alert",
							children: y
						}), /* @__PURE__ */ d("button", {
							className: "stira-restore-button",
							type: "button",
							onClick: () => void S(),
							disabled: _,
							children: _ ? "戻している…" : "木に戻す"
						})]
					}) : s ? /* @__PURE__ */ f("div", {
						className: "stira-prune-confirm",
						role: "alertdialog",
						"aria-labelledby": "stira-prune-title",
						"aria-describedby": "stira-prune-description",
						children: [/* @__PURE__ */ f("div", { children: [
							/* @__PURE__ */ d("strong", {
								id: "stira-prune-title",
								children: "この葉を剪定する？"
							}),
							/* @__PURE__ */ d("p", {
								id: "stira-prune-description",
								children: "木から外して落ち葉にする。剪定した葉は、あとで木に戻せる。"
							}),
							h && /* @__PURE__ */ d("p", {
								className: "stira-prune-error",
								role: "alert",
								children: h
							})
						] }), /* @__PURE__ */ f("div", {
							className: "stira-prune-confirm-actions",
							children: [/* @__PURE__ */ d("button", {
								type: "button",
								onClick: () => u(!1),
								disabled: p,
								children: "戻る"
							}), /* @__PURE__ */ d("button", {
								className: "stira-prune-button",
								type: "button",
								onClick: () => void x(),
								disabled: p,
								children: p ? "剪定中…" : "剪定する"
							})]
						})]
					}) : /* @__PURE__ */ d("button", {
						className: "stira-prune-button",
						type: "button",
						onClick: () => u(!0),
						children: "剪定する"
					})
				})
			]
		})]
	});
}
var N = "\n.stira-leaf-overlay { position: fixed; inset: 0; z-index: 50; display: grid; place-items: center; padding: 1rem; background: color-mix(in srgb, var(--bg, #0d0f12) 72%, transparent); backdrop-filter: blur(6px); }\n.stira-leaf-detail { display: grid; grid-template-rows: auto auto auto minmax(0, 1fr) auto; width: min(52rem, 96vw); max-height: min(90vh, 58rem); overflow: hidden; border: 1px solid var(--border, #353a42); border-radius: .9rem; background: var(--card, #171a1f); color: var(--text, #d9dde3); box-shadow: 0 1.4rem 5rem rgba(0,0,0,.58); }\n.stira-leaf-detail-header { display: flex; align-items: flex-start; justify-content: space-between; gap: 1rem; padding: 1rem 1.15rem .7rem; border-bottom: 1px solid var(--border, #353a42); }\n.stira-leaf-detail-header h2 { margin: .18rem 0 0; color: var(--text-strong, #fff); font-size: 1.18rem; line-height: 1.3; }\n.stira-leaf-detail-header button { flex: 0 0 auto; border: 1px solid transparent; border-radius: .4rem; padding: .12rem .48rem; background: transparent; color: var(--muted, #8f98a4); font-size: 1.45rem; cursor: pointer; }\n.stira-leaf-detail-header button:hover, .stira-leaf-detail-header button:focus-visible { border-color: var(--border, #353a42); color: var(--text-strong, #fff); outline: none; }\n.stira-leaf-eyebrow { margin: 0; color: var(--accent, #79a676); font-size: .68rem; font-weight: 700; letter-spacing: .08em; text-transform: uppercase; }\n.stira-leaf-summary { margin: 0; padding: .75rem 1.15rem .45rem; color: var(--muted, #a4acb7); font-size: .84rem; line-height: 1.55; }\n.stira-leaf-meta { display: flex; flex-wrap: wrap; gap: .35rem; padding: .2rem 1.15rem .8rem; }\n.stira-leaf-meta span { border: 1px solid color-mix(in srgb, var(--border, #353a42) 72%, transparent); border-radius: 999px; padding: .2rem .5rem; background: var(--bg-elevated, #22262d); color: var(--muted, #a4acb7); font-size: .68rem; }\n.stira-leaf-body { overflow: auto; padding: 0 1.15rem 1.3rem; scroll-behavior: smooth; overscroll-behavior: contain; }\n.stira-leaf-actions { display: flex; justify-content: flex-end; padding: .75rem 1.15rem; border-top: 1px solid var(--border, #353a42); background: color-mix(in srgb, var(--bg-elevated, #22262d) 54%, transparent); }\n.stira-prune-button, .stira-restore-button, .stira-prune-confirm-actions button { border: 1px solid var(--border, #353a42); border-radius: .5rem; padding: .5rem .75rem; background: var(--card, #171a1f); color: var(--text, #d9dde3); cursor: pointer; }\n.stira-restore-action { display: flex; align-items: center; gap: .65rem; }\n.stira-restore-action p { margin: 0; color: var(--danger, #ef6b73); font-size: .72rem; }\n.stira-restore-button { border-color: color-mix(in srgb, var(--accent, #79a676) 58%, var(--border, #353a42)); color: var(--accent, #79a676); }\n.stira-restore-button:hover:not(:disabled) { border-color: var(--accent, #79a676); color: var(--text-strong, #fff); }\n.stira-restore-button:disabled { opacity: .55; cursor: wait; }\n.stira-prune-button { border-color: color-mix(in srgb, var(--danger, #ef6b73) 58%, var(--border, #353a42)); color: var(--danger, #ef6b73); }\n.stira-prune-button:hover:not(:disabled) { border-color: var(--danger, #ef6b73); background: color-mix(in srgb, var(--danger, #ef6b73) 9%, var(--card, #171a1f)); }\n.stira-prune-confirm { display: flex; width: 100%; align-items: center; justify-content: space-between; gap: 1rem; }\n.stira-prune-confirm strong { color: var(--text-strong, #fff); font-size: .82rem; }\n.stira-prune-confirm p { margin: .2rem 0 0; color: var(--muted, #a4acb7); font-size: .72rem; line-height: 1.45; }\n.stira-prune-confirm .stira-prune-error { color: var(--danger, #ef6b73); }\n.stira-prune-confirm-actions { display: flex; flex: 0 0 auto; gap: .45rem; }\n.stira-prune-confirm-actions button:disabled { opacity: .55; cursor: wait; }\n.stira-leaf-toc { margin: .2rem 0 1rem; padding: .75rem .85rem; border: 1px solid var(--border, #353a42); border-radius: .65rem; background: color-mix(in srgb, var(--bg-elevated, #22262d) 72%, transparent); }\n.stira-leaf-toc strong { color: var(--text-strong, #fff); font-size: .72rem; letter-spacing: .06em; text-transform: uppercase; }\n.stira-leaf-toc ol { display: grid; gap: .2rem; margin: .45rem 0 0; padding: 0; list-style: none; }\n.stira-leaf-toc a { color: var(--accent, #8fc88b); font-size: .76rem; text-decoration: none; }\n.stira-leaf-toc a:hover { text-decoration: underline; }\n.stira-markdown { color: var(--text, #d9dde3); font-size: .86rem; line-height: 1.7; overflow-wrap: anywhere; }\n.stira-markdown h1, .stira-markdown h2, .stira-markdown h3, .stira-markdown h4, .stira-markdown h5, .stira-markdown h6 { scroll-margin-top: 1rem; margin: 1.35em 0 .45em; color: var(--text-strong, #fff); line-height: 1.35; }\n.stira-markdown h1 { font-size: 1.35rem; } .stira-markdown h2 { font-size: 1.18rem; } .stira-markdown h3 { font-size: 1.04rem; } .stira-markdown h4, .stira-markdown h5, .stira-markdown h6 { font-size: .92rem; }\n.stira-markdown p { margin: .65rem 0; }\n.stira-markdown a { color: var(--accent, #8fc88b); }\n.stira-markdown code { border-radius: .28rem; padding: .08rem .3rem; background: var(--bg, #0d0f12); color: var(--text-strong, #fff); font-family: ui-monospace, SFMono-Regular, Consolas, monospace; font-size: .82em; }\n.stira-markdown pre { overflow: auto; margin: .8rem 0; padding: .8rem; border: 1px solid var(--border, #353a42); border-radius: .55rem; background: var(--bg, #0d0f12); }\n.stira-markdown pre code { padding: 0; background: transparent; font-size: .78rem; white-space: pre; }\n.stira-markdown blockquote { margin: .8rem 0; padding: .15rem .85rem; border-left: 3px solid var(--accent, #79a676); color: var(--muted, #a4acb7); }\n.stira-markdown ul, .stira-markdown ol { padding-inline-start: 1.35rem; }\n.stira-markdown hr { border: 0; border-top: 1px solid var(--border, #353a42); margin: 1.2rem 0; }\n@media (max-width: 620px) { .stira-leaf-overlay { padding: .45rem; } .stira-leaf-detail { width: 100%; max-height: 96vh; } .stira-prune-confirm { align-items: stretch; flex-direction: column; } .stira-prune-confirm-actions { display: grid; grid-template-columns: 1fr 1fr; width: 100%; } }\n";
//#endregion
//#region src/components/LeafGlyph.tsx
function P({ shape: e, color: t, scale: n = 1 }) {
	let r = {
		fill: t,
		stroke: "var(--stira-leaf-edge)",
		strokeWidth: .7
	}, i = {
		fill: "none",
		stroke: "rgba(0,0,0,.32)",
		strokeWidth: .7
	};
	return /* @__PURE__ */ f("g", {
		className: `stira-leaf-glyph stira-leaf-glyph--${e}`,
		transform: `scale(${n})`,
		children: [
			e === "maple" && /* @__PURE__ */ f(u, { children: [/* @__PURE__ */ d("path", {
				d: "M 0 4.5 L -1.8 1.4 L -6 2.4 L -4 -1 L -8 -4 L -3 -3.8 L -2 -9 L 0 -5.4 L 2 -9 L 3 -3.8 L 8 -4 L 4 -1 L 6 2.4 L 1.8 1.4 Z",
				...r
			}), /* @__PURE__ */ d("path", {
				d: "M 0 4.2 L 0 -5.4",
				...i
			})] }),
			e === "broad" && /* @__PURE__ */ f(u, { children: [/* @__PURE__ */ d("path", {
				d: "M 0 -6.5 C 6.3 -1.95 4.9 4.55 0 6.5 C -4.9 4.55 -6.3 -1.95 0 -6.5 Z",
				...r
			}), /* @__PURE__ */ d("path", {
				d: "M 0 -5.5 L 0 5.5",
				...i
			})] }),
			e === "needle" && [
				-22,
				-11,
				0,
				11,
				22
			].map((e) => {
				let n = e * Math.PI / 180;
				return /* @__PURE__ */ d("line", {
					x1: "0",
					y1: "7.5",
					x2: Math.sin(n) * 15,
					y2: 7.5 - Math.cos(n) * 15,
					stroke: t,
					strokeWidth: "1.5",
					strokeLinecap: "round"
				}, e);
			}),
			e === "ginkgo" && /* @__PURE__ */ f(u, { children: [/* @__PURE__ */ d("path", {
				d: "M 0 5.5 L -8.25 -3.85 A 11 11 0 0 1 8.25 -3.85 Z",
				...r
			}), /* @__PURE__ */ d("path", {
				d: "M 0 5.5 L 0 -2.2",
				...i
			})] }),
			e === "heart" && /* @__PURE__ */ f(u, { children: [/* @__PURE__ */ d("path", {
				d: "M 0 4.95 C 9 0 5.4 -9 0 -3.15 C -5.4 -9 -9 0 0 4.95 Z",
				...r
			}), /* @__PURE__ */ d("path", {
				d: "M 0 3.6 L 0 -2.25",
				...i
			})] }),
			e === "slender" && /* @__PURE__ */ f(u, { children: [/* @__PURE__ */ d("path", {
				d: "M 0 -8 C 3.4 -1.6 3.4 4.8 0 8 C -3.4 4.8 -3.4 -1.6 0 -8 Z",
				...r
			}), /* @__PURE__ */ d("path", {
				d: "M 0 -6.7 L 0 6.7",
				...i
			})] })
		]
	});
}
//#endregion
//#region src/layout/hash.ts
function F(e) {
	let t = 2166136261;
	for (let n = 0; n < e.length; n += 1) t ^= e.charCodeAt(n), t = Math.imul(t, 16777619);
	return (t >>> 0) / 4294967296;
}
//#endregion
//#region src/layout/collide.ts
function I(e, t) {
	if (!e.anchor) return;
	let n = e.x - e.anchor.x, r = e.y - e.anchor.y, i = Math.hypot(n, r);
	if (i <= t || i === 0) return;
	let a = t / i;
	e.x = e.anchor.x + n * a, e.y = e.anchor.y + r * a;
}
function L(e, t) {
	let n = t.x - e.x, r = t.y - e.y, i = Math.hypot(n, r);
	if (i > 1e-9) return {
		x: n / i,
		y: r / i,
		distance: i
	};
	let a = e.id.localeCompare(t.id) <= 0 ? e.id : t.id, o = F(`${a}:${a === e.id ? t.id : e.id}:collision`) * Math.PI * 2;
	return {
		x: Math.cos(o),
		y: Math.sin(o),
		distance: 0
	};
}
function R(e, t, n = 0) {
	let r = 0;
	for (let i = 0; i < e.length; i += 1) for (let a = i + 1; a < e.length; a += 1) {
		let o = e[i], s = e[a], c = (o.hitRadius + s.hitRadius) * t;
		Math.hypot(s.x - o.x, s.y - o.y) + n < c && (r += 1);
	}
	return r;
}
function z(e, t) {
	let n = t.epsilon ?? .01, r = e.map((e) => ({
		...e,
		anchor: e.anchor ? { ...e.anchor } : null
	}));
	r.forEach((e) => I(e, t.maxAnchorDistance));
	let i = 0;
	for (let e = 0; e < t.iterations; e += 1) {
		let a = !1;
		for (let e = 0; e < r.length; e += 1) for (let i = e + 1; i < r.length; i += 1) {
			let o = r[e], s = r[i], c = (o.hitRadius + s.hitRadius) * t.packing, l = L(o, s), u = c - l.distance;
			if (u <= n) continue;
			let d = Math.max(.01, o.scale * o.scale), f = Math.max(.01, s.scale * s.scale), p = d + f, m = f / p * u, h = d / p * u;
			o.x -= l.x * m, o.y -= l.y * m, s.x += l.x * h, s.y += l.y * h, I(o, t.maxAnchorDistance), I(s, t.maxAnchorDistance), a = !0;
		}
		i = e + 1;
		let o = R(r, t.packing, n);
		if (!a || o === 0) break;
	}
	let a = 0;
	for (let e of r) e.anchor && (a = Math.max(a, Math.hypot(e.x - e.anchor.x, e.y - e.anchor.y)));
	return {
		leaves: r,
		overlapPairs: R(r, t.packing, n),
		relaxIterations: i,
		maxLeafAnchorDistance: a
	};
}
//#endregion
//#region src/layout/config.ts
var B = {
	viewWidth: 640,
	minViewHeight: 430,
	viewPadding: 38,
	groundOffset: 38,
	crownYBase: 72,
	crownGrowthLift: 12,
	bands: {
		core: .18,
		knowledge: .46,
		log: .74
	},
	lanePitch: 26,
	branchJitterY: 9,
	minBranchGap: 30,
	branchWidth: {
		core: 9,
		knowledge: 6,
		log: 3.5
	},
	branchGrowthWidth: 1.5,
	reachBase: 120,
	reachExtent: .45,
	reachLane: 11,
	reachJitter: 15,
	growthReach: 18,
	meansDriftReach: 34,
	meansDriftControl: 8,
	liftByLayer: {
		core: 30,
		knowledge: 20,
		log: 9
	},
	liftJitter: {
		min: .8,
		max: 1.3
	},
	controlReachRatio: .52,
	controlLiftRatio: 1.18,
	tMin: .28,
	tSpan: .6,
	tMax: .9,
	leafGapMin: .07,
	leafGapMax: .14,
	twigLengthMin: 12,
	twigLengthMax: 24,
	alongAmp: 14,
	bendAmp: 4,
	rotJitter: 10,
	layerScale: {
		core: 1.5,
		knowledge: 1.15,
		log: .9
	},
	scoreWeight: .5,
	scoreBase: .6,
	bulkWeight: .5,
	bulkChars: 2e3,
	hitRadiusBase: 10,
	hitRadiusScale: 6,
	witheringThreshold: .2,
	witheringDropMin: 5,
	witheringDropRange: 5,
	witheringRotation: 28,
	collisionPacking: .9,
	collisionIterations: 12,
	collisionEpsilon: .01,
	maxAnchorDistanceFactor: 1.6,
	denseBranchMargin: 8,
	layerBandGap: 24,
	labelCharWidth: 7,
	labelHeight: 14,
	labelGap: 4,
	labelShiftStep: 18,
	labelShiftAttempts: 4,
	stabilityFallback: .5,
	trunkBaseMin: 20,
	trunkBaseStability: 26,
	trunkTopMin: 8,
	trunkTopStability: 6,
	purposeDriftTilt: 24,
	domainExtentBase: 18,
	domainExtentCount: 12,
	domainExtentMax: 64,
	taggedDriftOffset: 22,
	meansLeafOffset: 12,
	activeLeafLimit: 100,
	fallenLeafLimit: 20,
	fallenSidePadding: 68,
	fallenVerticalRange: 10,
	opacityFreshMin: .72,
	opacityWithering: .58,
	rootCount: 5
};
function V(e = {}) {
	return {
		...B,
		...e,
		bands: {
			...B.bands,
			...e.bands
		},
		branchWidth: {
			...B.branchWidth,
			...e.branchWidth
		},
		liftByLayer: {
			...B.liftByLayer,
			...e.liftByLayer
		},
		liftJitter: {
			...B.liftJitter,
			...e.liftJitter
		},
		layerScale: {
			...B.layerScale,
			...e.layerScale
		}
	};
}
//#endregion
//#region src/layout/encoding.ts
var H = [
	[1, [
		90,
		175,
		80
	]],
	[.7, [
		150,
		190,
		60
	]],
	[.52, [
		220,
		190,
		55
	]],
	[.36, [
		224,
		140,
		50
	]],
	[.2, [
		200,
		70,
		55
	]],
	[.08, [
		120,
		55,
		45
	]],
	[0, [
		62,
		42,
		34
	]]
], U = [
	"maple",
	"broad",
	"needle",
	"ginkgo",
	"heart",
	"slender"
], W = {
	architecture: "maple",
	deployment: "broad",
	config: "needle",
	security: "ginkgo",
	operations: "heart",
	routing: "slender"
};
function G(e, t = 0) {
	return typeof e != "number" || !Number.isFinite(e) ? t : Math.max(0, Math.min(1, e));
}
function K(e) {
	let t = G(e);
	for (let e = 0; e < H.length - 1; e += 1) {
		let [n, r] = H[e], [i, a] = H[e + 1];
		if (t <= n && t >= i) {
			let e = (t - i) / (n - i), o = a.map((t, n) => Math.round(t + (r[n] - t) * e));
			return `rgb(${o[0]},${o[1]},${o[2]})`;
		}
	}
	return "rgb(62,42,34)";
}
function q(e) {
	let t = e.trim().toLocaleLowerCase();
	return W[t] ?? U[Math.floor(F(t || "general") * U.length) % U.length];
}
function J(e, t = B) {
	return Math.min(t.tMax, t.tMin + (1 - G(e)) * t.tSpan);
}
function Y(e, t, n = 0, r = B) {
	let i = G(t), a = G(n / r.bulkChars);
	return r.layerScale[e] * (r.scoreBase + i * r.scoreWeight) * (1 + a * r.bulkWeight);
}
//#endregion
//#region src/layout/engine.ts
var X = [
	"core",
	"knowledge",
	"log"
], Z = {
	core: 0,
	knowledge: 1,
	log: 2
};
function ne(e, t, n, r) {
	let i = 1 - r;
	return {
		x: i * i * e.x + 2 * i * r * t.x + r * r * n.x,
		y: i * i * e.y + 2 * i * r * t.y + r * r * n.y
	};
}
function re(e, t, n, r) {
	return {
		x: 2 * (1 - r) * (t.x - e.x) + 2 * r * (n.x - t.x),
		y: 2 * (1 - r) * (t.y - e.y) + 2 * r * (n.y - t.y)
	};
}
function ie(e, t) {
	let n = [...e].sort((e, t) => G(t.effectiveScore) - G(e.effectiveScore) || e.id.localeCompare(t.id));
	if (n.length === 0) return [];
	if (n.length === 1) return [{
		leaf: n[0],
		position: J(n[0].effectiveScore, t)
	}];
	let r = Math.max(t.leafGapMin, Math.min(t.leafGapMax, (t.tMax - t.tMin) / (n.length - 1))), i = n.map((e) => J(e.effectiveScore, t));
	for (let e = 1; e < i.length; e += 1) i[e] = Math.max(i[e], i[e - 1] + r);
	let a = Math.max(0, i[i.length - 1] - t.tMax);
	return n.map((e, t) => ({
		leaf: e,
		position: i[t] - a
	}));
}
function ae(e, t) {
	let n = /* @__PURE__ */ new Map();
	for (let t of e) {
		let e = t.domain?.trim() || "general";
		n.set(e, [...n.get(e) ?? [], t]);
	}
	let r = [...n.entries()].map(([e, n]) => ({
		domain: e,
		leaves: [...n].sort((e, t) => Z[e.layer] - Z[t.layer] || G(t.effectiveScore) - G(e.effectiveScore) || e.id.localeCompare(t.id)),
		closestLayer: n.reduce((e, t) => Z[t.layer] < Z[e] ? t.layer : e, "log"),
		extent: Math.min(t.domainExtentMax, t.domainExtentBase + Math.sqrt(n.length) * t.domainExtentCount)
	})).sort((e, t) => Z[e.closestLayer] - Z[t.closestLayer] || t.leaves.length - e.leaves.length || e.domain.localeCompare(t.domain)), i = {
		left: 0,
		right: 0
	};
	return r.map((e) => {
		let t = i.left <= i.right ? "left" : "right", n = t === "left" ? -1 : 1, r = i[t];
		return i[t] += 1, {
			...e,
			side: n,
			lane: r
		};
	});
}
function oe(e, t) {
	for (let n of [-1, 1]) {
		let r = e.filter((e) => e.group.side === n).sort((e, t) => e.startY - t.startY || e.group.domain.localeCompare(t.group.domain));
		for (let e = 1; e < r.length; e += 1) r[e].startY = Math.max(r[e].startY, r[e - 1].startY + t);
	}
}
function se(e, t, n, r, i) {
	let a = Math.max(i.labelCharWidth, n.length * i.labelCharWidth), o = r === "end" ? e - a : e;
	return {
		left: o - i.labelGap,
		right: o + a + i.labelGap,
		top: t - i.labelHeight - i.labelGap,
		bottom: t + i.labelGap
	};
}
function ce(e, t) {
	return e.left < t.right && e.right > t.left && e.top < t.bottom && e.bottom > t.top;
}
function le(e, t, n) {
	let r = t.map((e) => se(e.x, e.y, e.layer.toUpperCase(), "start", n)), i = e.filter((e) => e.label.visible).sort((e, t) => e.label.y - t.label.y || e.key.localeCompare(t.key));
	for (let e of i) {
		let t = !1, i = e.label.y;
		for (let a = 0; a <= n.labelShiftAttempts * 2; a += 1) {
			let o = Math.ceil(a / 2), s = i + (a === 0 ? 0 : a % 2 == 1 ? -1 : 1) * o * n.labelShiftStep, c = se(e.label.x, s, e.label.text, e.label.anchor, n);
			if (r.every((e) => !ce(c, e))) {
				e.label.y = s, r.push(c), t = !0;
				break;
			}
		}
		t || (e.label.visible = !1);
	}
}
function ue(e, t, n) {
	let r = t.x - e.x, i = t.y - e.y, a = Math.max(1, Math.hypot(r, i)), o = {
		x: -i / a,
		y: r / a
	};
	return `M ${e.x} ${e.y} Q ${(e.x + t.x) / 2 + o.x * n} ${(e.y + t.y) / 2 + o.y * n} ${t.x} ${t.y}`;
}
function de(e, t = {}) {
	let n = V(t), r = ae(e.leaves.filter((e) => !e.archived).slice(0, n.activeLeafLimit), n), i = Math.max(320, e.viewWidth ?? n.viewWidth), a = G(e.crew.growth), o = G(e.crew.stability, n.stabilityFallback), s = G(e.crew.purposeDrift), c = G(e.crew.meansDrift), l = n.crownYBase - a * n.crownGrowthLift, u = n.twigLengthMax * n.maxAnchorDistanceFactor, d = Math.max(n.lanePitch, u * 2 + n.denseBranchMargin), f = r.flatMap((e) => X.flatMap((t) => {
		let n = e.leaves.filter((e) => e.layer === t);
		return n.length > 0 ? [{
			group: e,
			layer: t,
			leaves: n
		}] : [];
	})), p = {
		core: {
			left: 0,
			right: 0
		},
		knowledge: {
			left: 0,
			right: 0
		},
		log: {
			left: 0,
			right: 0
		}
	};
	for (let e of f) p[e.layer][e.group.side < 0 ? "left" : "right"] += 1;
	let m = Object.fromEntries(X.map((e) => [e, Math.max(0, Math.max(p[e].left, p[e].right) - 1) * d])), h = [
		n.minViewHeight - n.groundOffset - l,
		(m.core / 2 + n.branchJitterY + n.layerBandGap) / n.bands.core,
		(m.log / 2 + n.branchJitterY + n.layerBandGap) / (1 - n.bands.log)
	];
	for (let e = 1; e < X.length; e += 1) {
		let t = X[e - 1], r = X[e], i = n.bands[r] - n.bands[t];
		i > 0 && h.push((m[t] / 2 + m[r] / 2 + n.branchJitterY * 2 + n.layerBandGap) / i);
	}
	let g = Math.max(...h), _ = l + g, v = Math.ceil(_ + n.groundOffset), y = i / 2, b = F(e.crew.name) >= .5 ? 1 : -1, x = y + s * n.purposeDriftTilt * b, S = n.trunkBaseMin + o * n.trunkBaseStability, C = n.trunkTopMin + o * n.trunkTopStability, w = (e) => G((e - l) / g), T = (e) => {
		let t = w(e), n = t * t * (3 - 2 * t);
		return x + (y - x) * n;
	}, ee = (e) => {
		let t = w(e);
		return C + (S - C) * t ** .86;
	}, E = [
		`M ${y - S / 2} ${_}`,
		`C ${y - S * .48} ${_ - g * .29}`,
		`${x - C * .72} ${l + g * .42}`,
		`${x - C / 2} ${l}`,
		`L ${x + C / 2} ${l}`,
		`C ${x + C * .72} ${l + g * .42}`,
		`${y + S * .48} ${_ - g * .29}`,
		`${y + S / 2} ${_}`,
		"Z"
	].join(" "), D = {
		core: {
			left: 0,
			right: 0
		},
		knowledge: {
			left: 0,
			right: 0
		},
		log: {
			left: 0,
			right: 0
		}
	}, O = f.map((e) => {
		let t = e.group.side < 0 ? "left" : "right", r = D[e.layer][t];
		D[e.layer][t] += 1;
		let i = r - (p[e.layer][t] - 1) / 2, a = (F(`${e.group.domain}:by`) * 2 - 1) * n.branchJitterY;
		return {
			...e,
			lane: r,
			startY: l + g * n.bands[e.layer] + i * d + a
		};
	});
	oe(O, d);
	let k = [], A = [];
	for (let e of O) {
		let { group: t, layer: r } = e, i = T(e.startY), o = {
			x: i + t.side * Math.max(2, ee(e.startY) / 2 - 1),
			y: e.startY
		}, s = (F(`${t.domain}:${r}:reach`) * 2 - 1) * n.reachJitter, l = n.reachBase + t.extent * n.reachExtent + e.lane * n.reachLane + a * n.growthReach + s, u = n.liftJitter.min + F(`${t.domain}:${r}:lift`) * (n.liftJitter.max - n.liftJitter.min), d = n.liftByLayer[r] * u, f = {
			x: i + t.side * (l + c * n.meansDriftReach),
			y: e.startY - d
		}, p = {
			x: o.x + t.side * (l * n.controlReachRatio - c * n.meansDriftControl),
			y: e.startY - d * n.controlLiftRatio
		}, m = `${t.domain}:${r}`, h = {
			key: m,
			domain: t.domain,
			layer: r,
			side: t.side,
			width: n.branchWidth[r] + a * n.branchGrowthWidth,
			path: `M ${o.x} ${o.y} Q ${p.x} ${p.y} ${f.x} ${f.y}`,
			start: o,
			control: p,
			end: f,
			label: {
				x: f.x + t.side * 14,
				y: f.y - 14,
				anchor: t.side < 0 ? "end" : "start",
				text: t.domain,
				visible: r === t.closestLayer
			}
		};
		k.push(h);
		for (let { leaf: i, position: a } of ie(e.leaves, n)) {
			let e = ne(o, p, f, a), s = re(o, p, f, a), l = Math.max(1, Math.hypot(s.x, s.y)), u = {
				x: s.x / l,
				y: s.y / l
			}, d = {
				x: -u.y,
				y: u.x
			}, h = G(i.effectiveScore), g = G(i.life, h), _ = g < n.witheringThreshold, v = _ || F(`${i.id}:twig-side`) >= .5 ? 1 : -1, y = n.twigLengthMin + F(`${i.id}:twig-length`) * (n.twigLengthMax - n.twigLengthMin), b = (F(`${i.id}:along-offset`) - .5) * n.alongAmp, x = (i.tags?.some((e) => /drift|deviation|逸脱/i.test(e)) ? n.taggedDriftOffset : 0) + c * n.meansLeafOffset, S = e.x + u.x * b + d.x * v * y + t.side * x, C = e.y + u.y * b + d.y * v * y + (_ ? n.witheringDropMin + F(`${i.id}:wither-drop`) * n.witheringDropRange : 0), w = Y(r, h, i.contentLength, n), T = Math.atan2(d.y * v, d.x * v) * 180 / Math.PI + 90 + (F(`${i.id}:r`) * 2 - 1) * n.rotJitter + (_ ? t.side * 25 + n.witheringRotation : 0);
			A.push({
				id: i.id,
				branchKey: m,
				x: S,
				y: C,
				rotation: T,
				scale: w,
				hitRadius: n.hitRadiusBase + w * n.hitRadiusScale,
				color: K(g),
				shape: q(i.domain),
				opacity: _ ? n.opacityWithering : n.opacityFreshMin + g * (1 - n.opacityFreshMin),
				withering: _,
				twigPath: null,
				anchor: e
			});
		}
	}
	let j = z(A, {
		packing: n.collisionPacking,
		iterations: n.collisionIterations,
		maxAnchorDistance: u,
		epsilon: n.collisionEpsilon
	}), M = j.leaves.map((e) => ({
		...e,
		twigPath: e.anchor ? ue(e.anchor, e, 2 + F(`${e.id}:twig-bend`) * n.bendAmp) : null
	})), te = (e.fallenLeaves ?? []).slice(0, n.fallenLeafLimit).map((e) => {
		let t = Math.min(G(e.life, e.effectiveScore), .04), r = .62 + F(`${e.id}:scale`) * .3;
		return {
			id: e.id,
			branchKey: null,
			x: n.fallenSidePadding + F(`${e.id}:x`) * (i - n.fallenSidePadding * 2),
			y: _ + 2 + F(`${e.id}:y`) * n.fallenVerticalRange,
			rotation: 30 + F(`${e.id}:rotation`) * 300,
			scale: r,
			hitRadius: n.hitRadiusBase + r * n.hitRadiusScale,
			color: K(t),
			shape: q(e.domain),
			opacity: .34 + F(`${e.id}:opacity`) * .2,
			withering: !0,
			twigPath: null,
			anchor: null
		};
	}), N = Array.from({ length: n.rootCount }, (e, t) => {
		let n = t % 2 == 0 ? -1 : 1, r = Math.floor(t / 2), i = 54 + r * 22 + a * 18;
		return {
			path: `M ${y + n * (2 + r * 2)} ${_ - r} Q ${y + n * i * .52} ${_ + 8 + r * 2} ${y + n * i} ${_ + 4 + r * 3}`,
			width: Math.max(2.5, S * .15 - r * .8)
		};
	}), P = X.map((e) => ({
		layer: e,
		x: 18,
		y: l + g * n.bands[e] - 7
	}));
	return le(k, P, n), {
		viewWidth: i,
		viewHeight: Math.ceil(Math.max(v, ...k.map((e) => Math.max(e.start.y, e.control.y, e.end.y, e.label.y) + n.viewPadding), ...M.map((e) => e.y + e.hitRadius + n.viewPadding), ...te.map((e) => e.y + e.hitRadius + n.viewPadding))),
		trunk: {
			path: E,
			rootX: y,
			rootY: _,
			crownY: l,
			baseWidth: S,
			topWidth: C
		},
		roots: N,
		branches: k,
		leaves: M,
		fallen: te,
		layerLabels: P,
		diagnostics: {
			overlapPairs: j.overlapPairs,
			relaxIterations: j.relaxIterations,
			maxLeafAnchorDistance: j.maxLeafAnchorDistance
		}
	};
}
//#endregion
//#region src/App.tsx
var Q = "/api/apps/stira", $ = "__custom__";
function fe(e) {
	if (e instanceof Error && /(?:409|harvest_in_progress)/i.test(e.message)) return !0;
	try {
		return /(?:"status"\s*:\s*409|harvest_in_progress)/i.test(JSON.stringify(e));
	} catch {
		return !1;
	}
}
function pe(e, t = 0) {
	return typeof e != "number" || !Number.isFinite(e) ? t : Math.max(0, Math.min(1, e));
}
function me(e) {
	if (e === !0) return 1;
	if (!e || e === !1 || typeof e != "object" || e.drifted === !1) return 0;
	for (let t of [
		e.score,
		e.magnitude,
		e.distance
	]) if (typeof t == "number" && Number.isFinite(t)) return pe(t);
	return Array.isArray(e.violations) && e.violations.length > 0 ? Math.min(1, .35 + e.violations.length * .15) : e.drifted ? .7 : 0;
}
function he(e) {
	return e === null ? "—" : `${Math.round(pe(e) * 100)}%`;
}
function ge(e) {
	return e.length <= 20 ? e : `${e.slice(0, 10)}…${e.slice(-8)}`;
}
function _e(e) {
	let t = Date.parse(e);
	return Number.isFinite(t) ? new Intl.DateTimeFormat("ja-JP", {
		dateStyle: "medium",
		timeStyle: "short"
	}).format(new Date(t)) : e || "時刻不明";
}
function ve(e) {
	return e.coreProposalPending ? "承認待ち" : e.onboardingRequired ? "未設定" : e.coreHashHealthy === !1 ? "要確認" : e.drift.detected ? "Drift detected" : e.coreHashHealthy === !0 ? "Aligned" : "Unknown";
}
function ye(e) {
	return e.coreProposalPending ? "proposal" : e.onboardingRequired ? "undefined" : e.coreHashHealthy === !1 ? "changed" : e.drift.core;
}
function be(e) {
	return e.coreProposalPending ? "芯の変更が人間の承認を待っている" : e.onboardingRequired ? "芯がまだ設定されていない" : e.coreHashHealthy === !1 ? "承認済みの芯と現在のIPDが一致していない" : e.drift.detected ? "目的または手段のドリフトを検出" : e.coreHashHealthy === !0 ? "承認済みの芯と一致" : "芯の状態を確認できない";
}
function xe(e) {
	return {
		id: e.id,
		domain: e.domain,
		layer: e.layer,
		effectiveScore: e.effectiveScore,
		life: e.life ?? e.effectiveScore,
		contentLength: e.contentLength,
		tags: e.tags,
		archived: e.archived,
		archivedReason: e.archivedReason
	};
}
function Se({ crew: e, leaves: t, fallenLeaves: n, onSelectLeaf: r }) {
	let i = me(e.drift.purpose), a = me(e.drift.means), o = s(() => ({
		crew: {
			name: e.name,
			stability: e.stability,
			growth: e.growth,
			purposeDrift: i,
			meansDrift: a
		},
		leaves: t.map(xe),
		fallenLeaves: n.map(xe)
	}), [
		e.growth,
		e.name,
		e.stability,
		n,
		t,
		a,
		i
	]), c = s(() => de(o), [o]), l = s(() => new Map(t.map((e) => [e.id, e])), [t]), u = s(() => new Map(n.map((e) => [e.id, e])), [n]), p = `trunk-${e.name}`, m = `glow-${e.name}`;
	return /* @__PURE__ */ f("svg", {
		className: "stira-tree",
		viewBox: `0 0 ${c.viewWidth} ${c.viewHeight}`,
		role: "img",
		"aria-label": `${e.name} knowledge tree with ${c.branches.length} domain branches`,
		children: [
			/* @__PURE__ */ f("defs", { children: [/* @__PURE__ */ f("linearGradient", {
				id: p,
				x1: "0",
				x2: "1",
				children: [
					/* @__PURE__ */ d("stop", {
						offset: "0",
						stopColor: "var(--stira-trunk-shadow)"
					}),
					/* @__PURE__ */ d("stop", {
						offset: "0.5",
						stopColor: "var(--stira-trunk)"
					}),
					/* @__PURE__ */ d("stop", {
						offset: "1",
						stopColor: "var(--stira-trunk-highlight)"
					})
				]
			}), /* @__PURE__ */ f("filter", {
				id: m,
				x: "-80%",
				y: "-80%",
				width: "260%",
				height: "260%",
				children: [/* @__PURE__ */ d("feGaussianBlur", {
					stdDeviation: "3",
					result: "blur"
				}), /* @__PURE__ */ f("feMerge", { children: [/* @__PURE__ */ d("feMergeNode", { in: "blur" }), /* @__PURE__ */ d("feMergeNode", { in: "SourceGraphic" })] })]
			})] }),
			/* @__PURE__ */ d("ellipse", {
				cx: c.trunk.rootX,
				cy: c.trunk.rootY + 7,
				rx: "112",
				ry: "13",
				fill: "var(--stira-ground)"
			}),
			c.roots.map((e, t) => /* @__PURE__ */ d("path", {
				d: e.path,
				fill: "none",
				stroke: "var(--stira-root)",
				strokeWidth: e.width,
				strokeLinecap: "round"
			}, `root-${t}`)),
			c.fallen.map((e) => {
				let t = u.get(e.id);
				if (!t) return null;
				let n = t.archivedReason === "pruned";
				return /* @__PURE__ */ f("g", {
					className: `stira-fallen-leaf${n ? " stira-fallen-leaf--restorable" : ""}`,
					transform: `translate(${e.x} ${e.y}) rotate(${e.rotation})`,
					opacity: e.opacity,
					role: n ? "button" : void 0,
					tabIndex: n ? 0 : void 0,
					"aria-label": n ? `${t.title}を木に戻す` : void 0,
					"aria-hidden": !n || void 0,
					onClick: n ? () => r(t) : void 0,
					onKeyDown: n ? (e) => {
						(e.key === "Enter" || e.key === " ") && (e.preventDefault(), r(t));
					} : void 0,
					children: [/* @__PURE__ */ d("circle", {
						r: e.hitRadius,
						fill: "transparent"
					}), /* @__PURE__ */ d(P, {
						shape: e.shape,
						color: e.color,
						scale: e.scale
					})]
				}, `fallen-${e.id}`);
			}),
			c.branches.map((e) => /* @__PURE__ */ d("path", {
				className: "stira-domain-branch",
				d: e.path,
				fill: "none",
				stroke: a > .2 ? "var(--stira-branch-drift)" : "var(--stira-branch)",
				strokeWidth: e.width,
				strokeLinecap: "round"
			}, e.key)),
			/* @__PURE__ */ d("path", {
				d: c.trunk.path,
				fill: `url(#${p})`
			}),
			c.layerLabels.map((e) => /* @__PURE__ */ d("text", {
				x: e.x,
				y: e.y,
				className: "stira-svg-label",
				children: e.layer.toUpperCase()
			}, e.layer)),
			c.branches.filter((e) => e.label.visible).map((e) => /* @__PURE__ */ d("text", {
				x: e.label.x,
				y: e.label.y,
				textAnchor: e.label.anchor,
				className: "stira-domain-label",
				children: e.label.text
			}, `${e.key}-label`)),
			c.leaves.map((e) => {
				let t = l.get(e.id);
				if (!t) return null;
				let n = t.life ?? t.effectiveScore;
				return /* @__PURE__ */ f("g", {
					className: `stira-leaf stira-leaf--${t.layer}${e.withering ? " stira-leaf--withering" : ""}`,
					role: "button",
					tabIndex: 0,
					"aria-label": `${t.title}, ${t.domain}, score ${Math.round(t.effectiveScore * 100)}%`,
					onClick: () => r(t),
					onKeyDown: (e) => {
						(e.key === "Enter" || e.key === " ") && (e.preventDefault(), r(t));
					},
					children: [
						e.twigPath && /* @__PURE__ */ d("path", {
							d: e.twigPath,
							fill: "none",
							stroke: "var(--stira-twig)",
							strokeWidth: "1",
							strokeLinecap: "round"
						}),
						/* @__PURE__ */ f("g", {
							transform: `translate(${e.x} ${e.y}) rotate(${e.rotation})`,
							opacity: e.opacity,
							filter: n > .78 ? `url(#${m})` : void 0,
							children: [/* @__PURE__ */ d("circle", {
								r: e.hitRadius,
								fill: "transparent"
							}), /* @__PURE__ */ d(P, {
								shape: e.shape,
								color: e.color,
								scale: e.scale
							})]
						}),
						/* @__PURE__ */ d("title", { children: `${t.title} · ${t.domain} · ${t.layer} · ${Math.round(t.effectiveScore * 100)}%` })
					]
				}, e.id);
			}),
			i > 0 && /* @__PURE__ */ d("path", {
				d: "M 42 34 L 62 34",
				stroke: "var(--stira-danger)",
				strokeWidth: "3",
				strokeLinecap: "round"
			}),
			a > 0 && /* @__PURE__ */ d("path", {
				d: "M 42 46 L 62 46",
				stroke: "var(--stira-warning)",
				strokeWidth: "3",
				strokeLinecap: "round"
			}),
			/* @__PURE__ */ d("text", {
				x: "68",
				y: "39",
				className: "stira-svg-label",
				children: i > 0 ? "purpose drift" : a > 0 ? "means drift" : ""
			})
		]
	});
}
function Ce({ proposal: e, onReview: t }) {
	return /* @__PURE__ */ f("section", {
		className: "stira-core-proposal",
		"aria-label": "承認待ちの芯の変更",
		children: [
			/* @__PURE__ */ f("div", {
				className: "stira-core-proposal-heading",
				children: [/* @__PURE__ */ f("div", { children: [/* @__PURE__ */ d("strong", { children: "これを植える？" }), /* @__PURE__ */ d("p", { children: "芯の変更が提案されている。内容を確認し、人間が承認するまで収穫の基準には使われない。" })] }), /* @__PURE__ */ d("span", { children: "承認待ち" })]
			}),
			e ? /* @__PURE__ */ f("div", {
				className: "stira-core-proposal-meta",
				children: [/* @__PURE__ */ f("div", {
					className: "stira-core-proposal-hashes",
					"aria-label": "芯のhash変更",
					children: [
						/* @__PURE__ */ d("code", {
							title: e.baseHash,
							children: ge(e.baseHash)
						}),
						/* @__PURE__ */ d("span", {
							"aria-hidden": "true",
							children: "→"
						}),
						/* @__PURE__ */ d("code", {
							title: e.candidateHash,
							children: ge(e.candidateHash)
						})
					]
				}), /* @__PURE__ */ d("time", {
					dateTime: e.proposedAt,
					children: _e(e.proposedAt)
				})]
			}) : /* @__PURE__ */ d("p", {
				className: "stira-core-proposal-missing",
				children: "提案の詳細を取得できない。更新して再確認してほしい。"
			}),
			/* @__PURE__ */ d("button", {
				type: "button",
				onClick: t,
				disabled: !e,
				children: "内容を確認して植える"
			})
		]
	});
}
function we({ crew: e, leaves: r, fallenLeaves: i, leafError: a, harvestState: o, harvestLog: s, harvestLogError: c, onHarvest: l, onRefreshHarvestLog: u, onReviewCoreProposal: p, onSelectLeaf: m }) {
	let h = _(s?.dispatch), g = o?.status === "running";
	return /* @__PURE__ */ f(t, { children: [
		/* @__PURE__ */ f("div", {
			className: "stira-card-header",
			children: [/* @__PURE__ */ f("div", { children: [/* @__PURE__ */ d(n, { children: e.name }), /* @__PURE__ */ d("p", {
				className: "stira-card-subtitle",
				children: be(e)
			})] }), /* @__PURE__ */ d("span", {
				className: `stira-status stira-status--${ye(e)}`,
				children: ve(e)
			})]
		}),
		e.coreProposalPending && /* @__PURE__ */ d(Ce, {
			proposal: e.pendingCoreProposal,
			onReview: p
		}),
		/* @__PURE__ */ d(Se, {
			crew: e,
			leaves: r,
			fallenLeaves: i,
			onSelectLeaf: m
		}),
		/* @__PURE__ */ f("div", {
			className: "stira-metrics",
			"aria-label": `${e.name} metrics`,
			children: [
				/* @__PURE__ */ f("span", { children: [/* @__PURE__ */ d("strong", { children: he(e.stability) }), " stability"] }),
				/* @__PURE__ */ f("span", { children: [/* @__PURE__ */ d("strong", { children: he(e.growth) }), " growth"] }),
				/* @__PURE__ */ f("span", { children: [/* @__PURE__ */ d("strong", { children: r.length }), " leaves"] })
			]
		}),
		/* @__PURE__ */ f("div", {
			className: "stira-harvest",
			children: [/* @__PURE__ */ d("button", {
				className: "stira-harvest-button",
				type: "button",
				onClick: l,
				disabled: h || e.onboardingRequired,
				children: h ? "収穫中…" : "今すぐ収穫"
			}), o && /* @__PURE__ */ d("p", {
				className: `stira-harvest-status stira-harvest-status--${o.status}`,
				role: o.status === "error" ? "alert" : "status",
				children: o.message
			})]
		}),
		/* @__PURE__ */ d(x, {
			runs: s?.runs ?? [],
			dispatch: s?.dispatch ?? null,
			pollingRequested: g,
			error: c,
			onRefresh: u
		}),
		a && /* @__PURE__ */ f("p", {
			className: "stira-inline-warning",
			children: ["Leaves unavailable: ", a]
		}),
		!a && r.length === 0 && /* @__PURE__ */ d("p", {
			className: "stira-empty-leaves",
			children: "No knowledge leaves yet."
		})
	] });
}
function Te() {
	let c = e(), [u, p] = l({
		crews: [],
		onboarding: null
	}), [m, h] = l({}), [g, _] = l({}), [v, y] = l({}), [b, x] = l({}), [S, C] = l({}), [w, T] = l(null), [E, D] = l(!0), [O, k] = l(null), [A, j] = l({}), [M, N] = l(!1), [P, F] = l([]), [I, L] = l($), [R, z] = l(""), [B, V] = l(""), [H, U] = l(!1), [W, G] = l(!1), [K, q] = l(null), [J, Y] = l(null), X = a(async () => {
		D(!0);
		try {
			let e = await c.get(`${Q}/crews`), t = await Promise.all(e.crews.map(async (e) => {
				let [t, n] = await Promise.all([c.get(`${Q}/crews/${encodeURIComponent(e.name)}/knowledge`).then((e) => ({
					value: e,
					error: null
				})).catch((t) => ({
					value: {
						crewId: e.name,
						entries: [],
						totalCount: 0
					},
					error: t instanceof Error ? t.message : "request failed"
				})), c.get(`${Q}/crews/${encodeURIComponent(e.name)}/harvest-log`).then((e) => ({
					value: e,
					error: null
				})).catch((t) => ({
					value: {
						crewId: e.name,
						dispatch: null,
						runs: []
					},
					error: t instanceof Error ? t.message : "request failed"
				}))]);
				return {
					crew: e.name,
					leafResult: t,
					logResult: n
				};
			}));
			p(e), j({
				version: e.version,
				buildHash: e.buildHash
			}), h(Object.fromEntries(t.map((e) => [e.crew, e.leafResult.value.entries]))), _(Object.fromEntries(t.filter((e) => e.leafResult.error).map((e) => [e.crew, e.leafResult.error]))), y(Object.fromEntries(t.map((e) => [e.crew, e.logResult.value]))), x(Object.fromEntries(t.filter((e) => e.logResult.error).map((e) => [e.crew, e.logResult.error]))), k(null);
		} catch (e) {
			k(e instanceof Error ? e.message : "Failed to load Stira forest");
		} finally {
			D(!1);
		}
	}, [c]), Z = a(async (e) => {
		try {
			let t = await c.get(`${Q}/crews/${encodeURIComponent(e)}/harvest-log`);
			y((n) => ({
				...n,
				[e]: t
			})), x((t) => {
				let n = { ...t };
				return delete n[e], n;
			});
		} catch (t) {
			let n = t instanceof Error ? t.message : "request failed";
			x((t) => ({
				...t,
				[e]: n
			}));
		}
	}, [c]), ne = a(async (e) => {
		C((t) => ({
			...t,
			[e]: {
				status: "running",
				message: "チャット履歴を収穫中…"
			}
		}));
		try {
			let t = await c.post(`${Q}/crews/${encodeURIComponent(e)}/harvest`, {});
			if (t.status === "skipped" && t.skip === "core_integrity") {
				C((t) => ({
					...t,
					[e]: {
						status: "security",
						message: "芯が承認と不一致のため収穫を停止"
					}
				})), await X();
				return;
			}
			let [n, r] = await Promise.all([c.get(`${Q}/crews/${encodeURIComponent(e)}/knowledge`), c.get(`${Q}/crews/${encodeURIComponent(e)}/harvest-log`)]);
			h((t) => ({
				...t,
				[e]: n.entries
			})), y((t) => ({
				...t,
				[e]: r
			})), _((t) => {
				let n = { ...t };
				return delete n[e], n;
			}), x((t) => {
				let n = { ...t };
				return delete n[e], n;
			});
			let i = r.runs[0], a = i?.writes?.written, o = i?.skips?.[0]?.reason ?? "reason not reported";
			C((t) => ({
				...t,
				[e]: {
					status: "success",
					message: a === 0 ? `no-op: ${o}` : `収穫完了。${a ?? 0}件を書いた。`
				}
			}));
		} catch (t) {
			if (fe(t)) {
				C((t) => ({
					...t,
					[e]: {
						status: "info",
						message: "既に収穫中"
					}
				})), await Z(e);
				return;
			}
			let n = t instanceof Error ? t.message : "request failed";
			C((t) => ({
				...t,
				[e]: {
					status: "error",
					message: `収穫に失敗: ${n}`
				}
			}));
		}
	}, [
		c,
		X,
		Z
	]);
	o(() => {
		X();
	}, [X]), o(() => {
		let e = !1;
		return c.get(`${Q}/agents`).then((t) => {
			if (e) return;
			let n = t;
			F(Array.isArray(n.agents) ? n.agents.filter((e) => typeof e == "string") : []);
		}).catch(() => {
			e || F([]);
		}), () => {
			e = !0;
		};
	}, [c]);
	let re = s(() => [.../* @__PURE__ */ new Set([...P, ...u.crews.map((e) => e.name)])], [P, u.crews]), ie = a(() => {
		let e = new Set(u.crews.map((e) => e.name)), t = P.find((t) => !e.has(t));
		L(t ?? $), z(""), V(""), U(!1), q(null), Y(null), N(!0);
	}, [P, u.crews]), ae = a((e) => {
		L(e), z(""), V(""), U(!0), q(null), Y(null), N(!0), window.requestAnimationFrame(() => {
			document.getElementById("stira-core-form")?.scrollIntoView({
				behavior: "smooth",
				block: "start"
			}), document.getElementById("stira-ipd-content")?.focus({ preventScroll: !0 });
		});
	}, []), oe = a(async (e) => {
		e.preventDefault();
		let t = I === $ ? R.trim() : I;
		if (!/^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/.test(t)) {
			q("Crew name must use only letters, numbers, dot, underscore, or hyphen.");
			return;
		}
		if (!B.trim()) {
			q("Define the crew core before growing its tree.");
			return;
		}
		G(!0), q(null);
		try {
			let e = await c.post(`${Q}/crews`, {
				crew_id: t,
				ipd_content: B,
				rebaseline: H
			});
			N(!1), V(""), z(""), U(!1), await X(), Y(e.rebaselined ? `${e.crewId} の新しい芯を承認して植えた。` : `${e.crewId} core registered. Its tree is now growing.`);
		} catch (e) {
			q(e instanceof Error ? e.message : "Failed to register crew core");
		} finally {
			G(!1);
		}
	}, [
		c,
		I,
		R,
		B,
		X,
		H
	]), se = a(async (e, t) => {
		await c.post(`${Q}/crews/${encodeURIComponent(e)}/leaves/${encodeURIComponent(t.id)}/prune`, {}), h((n) => ({
			...n,
			[e]: (n[e] ?? []).filter((e) => e.id !== t.id)
		})), p((n) => ({
			...n,
			crews: n.crews.map((n) => n.name === e ? {
				...n,
				fallenLeaves: [{
					...t,
					life: t.life ?? t.effectiveScore,
					archived: !0,
					archivedAt: (/* @__PURE__ */ new Date()).toISOString(),
					archivedReason: "pruned"
				}, ...(n.fallenLeaves ?? []).filter((e) => e.id !== t.id)].slice(0, 20)
			} : n)
		}));
	}, [c]), ce = a(async (e, t) => {
		await c.post(`${Q}/crews/${encodeURIComponent(e)}/leaves/${encodeURIComponent(t.id)}/unprune`, {}), await X();
	}, [c, X]), le = s(() => ({
		leaves: Object.values(m).reduce((e, t) => e + t.length, 0),
		drifted: u.crews.filter((e) => e.drift.detected).length,
		healthy: u.crews.filter((e) => e.coreHashHealthy === !0).length
	}), [u.crews, m]);
	return /* @__PURE__ */ f("div", {
		className: "stira-page",
		children: [
			/* @__PURE__ */ d("style", { children: Ee }),
			/* @__PURE__ */ d(r, {
				title: "Stira Forest",
				subtitle: "Mission stability, mastery, knowledge, and drift — one living tree per crew."
			}),
			/* @__PURE__ */ f("div", {
				className: "stira-content",
				children: [
					/* @__PURE__ */ f("div", {
						className: "stira-toolbar",
						children: [/* @__PURE__ */ f("div", {
							className: "stira-stats",
							children: [
								/* @__PURE__ */ d(i, {
									label: "Crew trees",
									value: String(u.crews.length),
									accent: !0
								}),
								/* @__PURE__ */ d(i, {
									label: "Healthy cores",
									value: String(le.healthy)
								}),
								/* @__PURE__ */ d(i, {
									label: "Knowledge leaves",
									value: String(le.leaves)
								}),
								/* @__PURE__ */ d(i, {
									label: "Drift signals",
									value: String(le.drifted)
								})
							]
						}), /* @__PURE__ */ f("div", {
							className: "stira-toolbar-actions",
							children: [/* @__PURE__ */ d("button", {
								className: "stira-add",
								type: "button",
								onClick: ie,
								children: "Add crew"
							}), /* @__PURE__ */ d("button", {
								className: "stira-refresh",
								type: "button",
								onClick: () => void X(),
								disabled: E,
								children: E ? "Loading…" : "Refresh forest"
							})]
						})]
					}),
					O && /* @__PURE__ */ d("div", {
						className: "stira-error",
						role: "alert",
						children: O
					}),
					J && /* @__PURE__ */ d("div", {
						className: "stira-success",
						role: "status",
						children: J
					}),
					u.onboarding?.required && /* @__PURE__ */ f("div", {
						className: "stira-onboarding",
						children: [/* @__PURE__ */ f("div", { children: [/* @__PURE__ */ d("strong", { children: u.onboarding.question ?? "Define each crew core before growth begins." }), /* @__PURE__ */ d("span", { children: (u.onboarding.crews ?? []).length > 0 ? ` Waiting: ${(u.onboarding.crews ?? []).join(", ")}` : " No crews are configured." })] }), /* @__PURE__ */ d("button", {
							type: "button",
							onClick: ie,
							children: "Define a core"
						})]
					}),
					M && /* @__PURE__ */ d(t, { children: /* @__PURE__ */ f("form", {
						id: "stira-core-form",
						className: "stira-core-form",
						onSubmit: (e) => void oe(e),
						children: [
							/* @__PURE__ */ f("div", {
								className: "stira-form-heading",
								children: [/* @__PURE__ */ f("div", { children: [/* @__PURE__ */ d(n, { children: H ? "Review and plant a core change" : "Grow a crew tree" }), /* @__PURE__ */ d("p", {
									className: "stira-muted",
									children: H ? "Stiraは提案のhashだけを保持する。内容を確認し、完全なcandidate IPDを貼り付けて承認する。" : "Choose the crew and register its immutable IPD core."
								})] }), /* @__PURE__ */ d("button", {
									className: "stira-form-close",
									type: "button",
									"aria-label": "Close crew form",
									onClick: () => N(!1),
									children: "×"
								})]
							}),
							/* @__PURE__ */ f("label", { children: ["Crew", /* @__PURE__ */ f("select", {
								value: I,
								onChange: (e) => L(e.target.value),
								children: [re.map((e) => /* @__PURE__ */ f("option", {
									value: e,
									children: [
										e,
										e === "default" ? " (conductor)" : "",
										u.crews.some((t) => t.name === e) ? " (registered)" : ""
									]
								}, e)), /* @__PURE__ */ d("option", {
									value: $,
									children: "Custom crew name…"
								})]
							})] }),
							I === $ && /* @__PURE__ */ f("label", { children: ["Custom crew name", /* @__PURE__ */ d("input", {
								value: R,
								onChange: (e) => z(e.target.value),
								placeholder: "research-operator",
								autoComplete: "off",
								maxLength: 64
							})] }),
							/* @__PURE__ */ f("label", { children: ["Core (IPD)", /* @__PURE__ */ d("textarea", {
								id: "stira-ipd-content",
								value: B,
								onChange: (e) => V(e.target.value),
								placeholder: "# P0 — Mission\\nDefine what this crew exists to protect and accomplish.",
								rows: 9
							})] }),
							/* @__PURE__ */ f("label", {
								className: "stira-rebaseline-option",
								children: [/* @__PURE__ */ d("input", {
									type: "checkbox",
									checked: H,
									onChange: (e) => U(e.target.checked)
								}), /* @__PURE__ */ f("span", { children: [/* @__PURE__ */ d("strong", { children: "承認済みの芯として再設定する" }), /* @__PURE__ */ d("small", { children: "既存の承認基準を、この欄に貼り付けたIPDへ更新する。" })] })]
							}),
							K && /* @__PURE__ */ d("div", {
								className: "stira-form-error",
								role: "alert",
								children: K
							}),
							/* @__PURE__ */ f("div", {
								className: "stira-form-actions",
								children: [/* @__PURE__ */ d("button", {
									type: "button",
									onClick: () => N(!1),
									disabled: W,
									children: "Cancel"
								}), /* @__PURE__ */ d("button", {
									className: "stira-add",
									type: "submit",
									disabled: W,
									children: W ? H ? "Planting…" : "Growing…" : H ? "内容を確認して植える" : "Register core and grow tree"
								})]
							})
						]
					}) }),
					/* @__PURE__ */ d(ee, {}),
					!E && !O && u.crews.length === 0 && /* @__PURE__ */ f(t, { children: [/* @__PURE__ */ d(n, { children: "The forest is empty" }), /* @__PURE__ */ d("p", {
						className: "stira-muted",
						children: "Configure a crew and define its IPD core to grow the first tree."
					})] }),
					/* @__PURE__ */ d("div", {
						className: "stira-forest",
						children: u.crews.map((e) => /* @__PURE__ */ d(we, {
							crew: e,
							leaves: m[e.name] ?? [],
							fallenLeaves: e.fallenLeaves ?? [],
							leafError: g[e.name],
							harvestState: S[e.name],
							harvestLog: v[e.name],
							harvestLogError: b[e.name],
							onHarvest: () => void ne(e.name),
							onRefreshHarvestLog: () => Z(e.name),
							onReviewCoreProposal: () => ae(e.name),
							onSelectLeaf: (t) => T({
								crew: e.name,
								leaf: t
							})
						}, e.name))
					}),
					w && /* @__PURE__ */ d(te, {
						crew: w.crew,
						leaf: w.leaf,
						onPrune: w.leaf.archived ? void 0 : () => se(w.crew, w.leaf),
						onUnprune: w.leaf.archivedReason === "pruned" ? () => ce(w.crew, w.leaf) : void 0,
						onClose: () => T(null)
					}),
					/* @__PURE__ */ f("div", {
						className: "stira-build-info",
						"data-testid": "stira-build-info",
						children: [
							"v",
							A.version ?? "?",
							" · build ",
							A.buildHash ?? "?"
						]
					})
				]
			})
		]
	});
}
var Ee = "\n.stira-page {\n  --stira-trunk: var(--accent, #8f744f);\n  --stira-trunk-shadow: color-mix(in srgb, var(--accent, #8f744f) 45%, #18120d);\n  --stira-trunk-highlight: color-mix(in srgb, var(--accent, #8f744f) 55%, #e5c891);\n  --stira-root: color-mix(in srgb, var(--accent, #8f744f) 36%, #302319);\n  --stira-branch: color-mix(in srgb, var(--accent, #8f744f) 52%, #38271b);\n  --stira-branch-drift: var(--warning, #e4a853);\n  --stira-twig: color-mix(in srgb, var(--accent, #8f744f) 42%, var(--border, #353a42));\n  --stira-leaf-edge: color-mix(in srgb, var(--border, #3b4148) 68%, transparent);\n  --stira-ground: color-mix(in srgb, var(--card, #171a1f) 75%, #0a0c0f);\n  --stira-danger: var(--danger, #ef6b73);\n  --stira-warning: var(--warning, #e4a853);\n  min-height: 100%;\n  color: var(--text, #d9dde3);\n  background:\n    radial-gradient(circle at 15% 0%, color-mix(in srgb, var(--accent, #7b9e78) 8%, transparent), transparent 34rem),\n    var(--bg, #0d0f12);\n}\n.stira-content { padding: 0 1.5rem 2rem; }\n.stira-build-info { margin-top: 1.25rem; text-align: right; color: var(--muted, #8f98a4); font-size: .68rem; letter-spacing: .04em; font-variant-numeric: tabular-nums; }\n.stira-toolbar { display: flex; align-items: flex-end; gap: 1rem; margin-bottom: 1rem; }\n.stira-stats { display: grid; grid-template-columns: repeat(4, minmax(8.5rem, 1fr)); gap: .75rem; flex: 1; }\n.stira-toolbar-actions { display: flex; gap: .55rem; }\n.stira-refresh, .stira-add, .stira-onboarding button, .stira-form-actions button {\n  border: 1px solid var(--border, #353a42); border-radius: .55rem; padding: .62rem .9rem;\n  background: var(--card, #171a1f); color: var(--text, #d9dde3); cursor: pointer; white-space: nowrap;\n}\n.stira-add { border-color: color-mix(in srgb, var(--accent, #79a676) 62%, transparent); background: color-mix(in srgb, var(--accent, #79a676) 14%, var(--card, #171a1f)); color: var(--text-strong, #fff); }\n.stira-refresh:hover:not(:disabled), .stira-add:hover:not(:disabled), .stira-onboarding button:hover:not(:disabled), .stira-form-actions button:hover:not(:disabled) { border-color: var(--accent, #79a676); color: var(--text-strong, #fff); }\n.stira-refresh:disabled, .stira-add:disabled, .stira-form-actions button:disabled { opacity: .55; cursor: wait; }\n.stira-forest { display: grid; grid-template-columns: repeat(auto-fit, minmax(22rem, 1fr)); gap: 1rem; align-items: start; }\n.stira-card-header { display: flex; justify-content: space-between; gap: 1rem; align-items: flex-start; }\n.stira-card-subtitle { margin: .15rem 0 0; color: var(--muted, #8f98a4); font-size: .78rem; }\n.stira-status { border: 1px solid var(--border, #353a42); border-radius: 999px; padding: .2rem .55rem; color: var(--muted, #8f98a4); font-size: .68rem; text-transform: uppercase; letter-spacing: .07em; }\n.stira-status--healthy { border-color: color-mix(in srgb, var(--ok, #6ecf8d) 45%, transparent); color: var(--ok, #6ecf8d); }\n.stira-status--proposal { border-color: color-mix(in srgb, var(--warning, #e4a853) 58%, transparent); background: color-mix(in srgb, var(--warning, #e4a853) 10%, transparent); color: var(--warning, #e4a853); }\n.stira-status--changed { border-color: color-mix(in srgb, var(--danger, #ef6b73) 50%, transparent); color: var(--danger, #ef6b73); }\n.stira-status--undefined { border-color: color-mix(in srgb, var(--warning, #e4a853) 50%, transparent); color: var(--warning, #e4a853); }\n/* ── A-1 pending core proposal approval UI ───────────────────────────── */\n.stira-core-proposal { display: grid; gap: .65rem; margin-top: .85rem; padding: .8rem; border: 1px solid color-mix(in srgb, var(--warning, #e4a853) 48%, transparent); border-radius: .65rem; background: color-mix(in srgb, var(--warning, #e4a853) 8%, var(--card, #171a1f)); }\n.stira-core-proposal-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: .75rem; }\n.stira-core-proposal-heading strong { color: var(--warning, #e4a853); font-size: .9rem; }\n.stira-core-proposal-heading p, .stira-core-proposal-missing { margin: .2rem 0 0; color: var(--muted, #8f98a4); font-size: .72rem; line-height: 1.45; }\n.stira-core-proposal-heading > span { border: 1px solid color-mix(in srgb, var(--warning, #e4a853) 50%, transparent); border-radius: 999px; padding: .16rem .45rem; color: var(--warning, #e4a853); font-size: .65rem; white-space: nowrap; }\n.stira-core-proposal-meta { display: flex; align-items: center; justify-content: space-between; gap: .75rem; color: var(--muted, #8f98a4); font-size: .68rem; }\n.stira-core-proposal-hashes { display: flex; align-items: center; gap: .35rem; min-width: 0; }\n.stira-core-proposal-hashes code { color: var(--text, #d9dde3); overflow-wrap: anywhere; }\n.stira-core-proposal time { white-space: nowrap; }\n.stira-core-proposal > button { justify-self: start; border: 1px solid color-mix(in srgb, var(--warning, #e4a853) 58%, transparent); border-radius: .5rem; padding: .48rem .7rem; background: color-mix(in srgb, var(--warning, #e4a853) 12%, var(--card, #171a1f)); color: var(--text-strong, #fff); cursor: pointer; }\n.stira-core-proposal > button:hover:not(:disabled) { border-color: var(--warning, #e4a853); }\n.stira-core-proposal > button:disabled { opacity: .55; cursor: not-allowed; }\n/* ── End A-1 pending core proposal approval UI ───────────────────────── */\n.stira-tree { display: block; width: 100%; height: auto; margin: .25rem auto -.35rem; overflow: visible; }\n.stira-svg-label { fill: var(--muted, #8f98a4); font-size: 10px; letter-spacing: .04em; }\n.stira-domain-label { fill: var(--muted, #a4acb7); font-size: 10px; font-weight: 650; letter-spacing: .025em; paint-order: stroke; stroke: var(--card, #171a1f); stroke-width: 3px; stroke-linejoin: round; }\n.stira-domain-branch { transition: stroke .15s ease, stroke-width .15s ease; }\n.stira-domain-branch:hover { stroke: color-mix(in srgb, var(--accent, #79a676) 75%, var(--stira-branch)); }\n.stira-leaf { cursor: pointer; outline: none; transition: opacity .15s ease; }\n.stira-leaf--withering .stira-leaf-glyph { filter: saturate(.78); }\n.stira-leaf:hover .stira-leaf-glyph path, .stira-leaf:focus .stira-leaf-glyph path,\n.stira-leaf:hover .stira-leaf-glyph line, .stira-leaf:focus .stira-leaf-glyph line { stroke: var(--text-strong, #fff); stroke-width: 1.7; }\n.stira-leaf-glyph path, .stira-leaf-glyph line { transition: stroke .15s ease, stroke-width .15s ease; }\n.stira-fallen-leaf { pointer-events: none; }\n.stira-fallen-leaf--restorable { pointer-events: auto; cursor: pointer; outline: none; }\n.stira-fallen-leaf--restorable:hover .stira-leaf-glyph path,\n.stira-fallen-leaf--restorable:focus .stira-leaf-glyph path,\n.stira-fallen-leaf--restorable:hover .stira-leaf-glyph line,\n.stira-fallen-leaf--restorable:focus .stira-leaf-glyph line { stroke: var(--text-strong, #fff); stroke-width: 1.7; }\n.stira-metrics { display: grid; grid-template-columns: repeat(3, 1fr); border-top: 1px solid var(--border, #353a42); padding-top: .8rem; color: var(--muted, #8f98a4); font-size: .72rem; text-align: center; }\n.stira-metrics strong { display: block; color: var(--text-strong, #fff); font-size: .95rem; }\n.stira-harvest { display: grid; justify-items: center; gap: .35rem; margin-top: .8rem; }\n.stira-harvest-button {\n  border: 1px solid color-mix(in srgb, var(--accent, #79a676) 52%, var(--border, #353a42));\n  border-radius: .55rem; padding: .55rem .9rem; background: color-mix(in srgb, var(--accent, #79a676) 10%, var(--card, #171a1f));\n  color: var(--text-strong, #fff); cursor: pointer;\n}\n.stira-harvest-button:hover:not(:disabled) { border-color: var(--accent, #79a676); }\n.stira-harvest-button:disabled { opacity: .55; cursor: wait; }\n.stira-harvest-status { margin: 0; color: var(--muted, #8f98a4); font-size: .72rem; text-align: center; }\n.stira-harvest-status--success { color: var(--ok, #6ecf8d); }\n.stira-harvest-status--info { color: var(--accent, #79a676); }\n.stira-harvest-status--security { padding: .4rem .55rem; border: 1px solid color-mix(in srgb, var(--warning, #e4a853) 45%, transparent); border-radius: .45rem; background: color-mix(in srgb, var(--warning, #e4a853) 8%, transparent); color: var(--warning, #e4a853); font-weight: 650; }\n.stira-harvest-status--error { color: var(--danger, #ef6b73); }\n.stira-empty-leaves, .stira-inline-warning { margin: .6rem 0 0; text-align: center; color: var(--muted, #8f98a4); font-size: .72rem; }\n.stira-inline-warning { color: var(--warning, #e4a853); }\n.stira-onboarding, .stira-error, .stira-success { margin-bottom: 1rem; padding: .8rem 1rem; border-radius: .65rem; border: 1px solid color-mix(in srgb, var(--warning, #e4a853) 45%, transparent); background: color-mix(in srgb, var(--warning, #e4a853) 9%, var(--card, #171a1f)); color: var(--text, #d9dde3); font-size: .82rem; }\n.stira-onboarding { display: flex; align-items: center; justify-content: space-between; gap: 1rem; }\n.stira-onboarding strong { color: var(--warning, #e4a853); }\n.stira-onboarding span { margin-left: .25rem; }\n.stira-onboarding button { padding-block: .45rem; }\n.stira-error { border-color: color-mix(in srgb, var(--danger, #ef6b73) 45%, transparent); background: color-mix(in srgb, var(--danger, #ef6b73) 9%, var(--card, #171a1f)); color: var(--danger, #ef6b73); }\n.stira-success { border-color: color-mix(in srgb, var(--ok, #6ecf8d) 45%, transparent); background: color-mix(in srgb, var(--ok, #6ecf8d) 9%, var(--card, #171a1f)); color: var(--ok, #6ecf8d); }\n.stira-core-form { display: grid; gap: .85rem; margin-bottom: 1rem; }\n.stira-form-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 1rem; }\n.stira-form-close { border: 0; background: transparent; color: var(--muted, #8f98a4); font-size: 1.4rem; cursor: pointer; }\n.stira-core-form label { display: grid; gap: .35rem; color: var(--muted, #8f98a4); font-size: .75rem; font-weight: 600; }\n.stira-core-form select, .stira-core-form input, .stira-core-form textarea { width: 100%; box-sizing: border-box; border: 1px solid var(--border, #353a42); border-radius: .55rem; padding: .65rem .72rem; background: var(--bg-elevated, #22262d); color: var(--text, #d9dde3); font: inherit; }\n.stira-core-form textarea { min-height: 10rem; resize: vertical; font-family: ui-monospace, SFMono-Regular, Consolas, monospace; line-height: 1.45; }\n.stira-core-form select:focus, .stira-core-form input:focus, .stira-core-form textarea:focus { outline: 2px solid color-mix(in srgb, var(--accent, #79a676) 45%, transparent); border-color: var(--accent, #79a676); }\n.stira-rebaseline-option { grid-template-columns: auto 1fr !important; align-items: start; padding: .7rem; border: 1px solid color-mix(in srgb, var(--warning, #e4a853) 40%, var(--border, #353a42)); border-radius: .55rem; background: color-mix(in srgb, var(--warning, #e4a853) 6%, transparent); cursor: pointer; }\n.stira-rebaseline-option input { width: auto; margin: .15rem 0 0; padding: 0; border: 0; border-radius: 0; background: transparent; accent-color: var(--warning, #e4a853); }\n.stira-rebaseline-option span { display: grid; gap: .2rem; }\n.stira-rebaseline-option strong { color: var(--text, #d9dde3); }\n.stira-rebaseline-option small { color: var(--muted, #8f98a4); font-weight: 400; line-height: 1.4; }\n.stira-form-error { color: var(--danger, #ef6b73); font-size: .78rem; }\n.stira-form-actions { display: flex; justify-content: flex-end; gap: .55rem; }\n.stira-muted { color: var(--muted, #8f98a4); font-size: .85rem; }\n\n@media (max-width: 760px) {\n  .stira-content { padding-inline: .8rem; }\n  .stira-toolbar { align-items: stretch; flex-direction: column; }\n  .stira-toolbar-actions, .stira-form-actions { display: grid; grid-template-columns: 1fr; }\n  .stira-onboarding { align-items: stretch; flex-direction: column; }\n  .stira-core-proposal-meta { align-items: flex-start; flex-direction: column; }\n  .stira-core-proposal-hashes { flex-wrap: wrap; }\n  .stira-stats { grid-template-columns: repeat(2, 1fr); }\n  .stira-forest { grid-template-columns: 1fr; }\n}\n";
//#endregion
export { Te as default };
