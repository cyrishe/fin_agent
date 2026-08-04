import {
  ArrowRight,
  Activity,
  BarChart3,
  Bot,
  CheckCircle2,
  Database,
  FileSearch,
  Layers3,
  LineChart,
  Search,
  Send,
  ShieldCheck,
  Sparkles,
  Upload,
  Workflow,
} from "lucide-react";
import { type FormEvent, type KeyboardEvent, useRef, useState } from "react";
import "./landing.css";

export const PENDING_PROMPT_STORAGE_KEY = "fin_agent.pending_prompt";

export function continueToRegistration(
  prompt: string,
  storage: Pick<Storage, "setItem">,
  navigate: (path: string) => void,
): boolean {
  const normalizedPrompt = prompt.trim();
  if (!normalizedPrompt) return false;
  storage.setItem(PENDING_PROMPT_STORAGE_KEY, normalizedPrompt);
  navigate("/register");
  return true;
}

const suggestions = [
  "分析贵州茅台今天的行情，并说明主要变化",
  "找出近 20 个交易日动量强且成交稳定的股票",
  "把这个选股想法做成策略，回测过去三年",
  "用我的风险评分 Skill 分析附件中的公司",
];

const demos = [
  {
    key: "research",
    label: "金融问答",
    eyebrow: "从问题到有依据的回答",
    prompt: "贵州茅台今天的行情有什么变化？",
    steps: ["识别证券与时间口径", "查询行情与相关资料", "组织结论与可核查证据"],
    resultTitle: "行情概览已形成",
    resultCopy: "先给出关键变化，再展开量价、相关信息和数据时间；每一步都保留可核查线索。",
    resultTags: ["交易日已确认", "数据时间可见", "依据可展开"],
  },
  {
    key: "strategy",
    label: "构建策略",
    eyebrow: "把模糊想法讲成可执行规则",
    prompt: "寻找增长稳健、估值不过高且近期走势较强的公司",
    steps: ["理解目标与风险偏好", "澄清指标和调仓口径", "形成可解释的策略草案"],
    resultTitle: "策略逻辑等待确认",
    resultCopy: "自然语言负责交流与修订，稳定的策略规则负责执行；关键口径在运行前清楚呈现。",
    resultTags: ["候选范围", "选股条件", "调仓规则"],
  },
  {
    key: "backtest",
    label: "运行回测",
    eyebrow: "让历史验证回答策略问题",
    prompt: "用过去三年的交易日数据验证这套策略",
    steps: ["固定数据区间与交易假设", "按规则生成组合与调仓", "汇总收益、风险、成本和持仓"],
    resultTitle: "不只展示一条收益曲线",
    resultCopy: "回测同时解释最大回撤、换手、交易成本与持仓变化，让结果能够复查，而不是只看一个数字。",
    resultTags: ["收益与回撤", "持仓过程", "交易成本"],
  },
  {
    key: "skill",
    label: "调用 Skill",
    eyebrow: "把成熟方法变成个人能力",
    prompt: "$company_risk_score 分析附件中的公司",
    steps: ["解析附件与公司列表", "调用指定 Skill 执行", "有计划地汇总批量结果"],
    resultTitle: "一次方法，以后自然调用",
    resultCopy: "Skill 保留明确用途和输入边界，系统负责复杂输入、批量运行与适合金融场景的结果呈现。",
    resultTags: ["附件输入", "批量执行", "结果汇总"],
  },
] as const;

const capabilities = [
  {
    number: "01",
    title: "金融查询与问答",
    description: "直接问行情、财务、研报和市场数据。回答优先突出结论，也把数据时间、口径和证据留在手边。",
    proof: ["自然语言查询", "过程线索可见", "金融图表与表格"],
    icon: Search,
    tone: "blue",
  },
  {
    number: "02",
    title: "自然交互的策略平台",
    description: "从一句不完整的投资想法开始，通过对话澄清范围、指标与调仓方式，逐步形成可执行策略。",
    proof: ["对话式澄清", "规则可解释", "策略持续修订"],
    icon: Workflow,
    tone: "gold",
  },
  {
    number: "03",
    title: "回测与策略管理",
    description: "围绕组合、持仓和交易验证策略，并保留运行假设、结果和版本，方便比较与继续迭代。",
    proof: ["组合级回测", "成本与回撤", "运行记录可复查"],
    icon: LineChart,
    tone: "ink",
  },
  {
    number: "04",
    title: "强大的 Skill 平台",
    description: "把反复使用的分析方法沉淀为 Skill。既可以直接调用，也能结合文件和股票列表完成批量任务。",
    proof: ["个人能力沉淀", "文件与批量输入", "版本化复用"],
    icon: Layers3,
    tone: "green",
  },
] as const;

const workflow = [
  {
    number: "01",
    title: "查清事实",
    copy: "从行情、财务、研报和市场信息中找到回答所需证据。",
    icon: FileSearch,
  },
  {
    number: "02",
    title: "形成策略",
    copy: "把研究想法转成能解释、能确认、能执行的策略规则。",
    icon: Workflow,
  },
  {
    number: "03",
    title: "历史验证",
    copy: "通过组合、持仓、交易成本和风险指标检验策略表现。",
    icon: BarChart3,
  },
  {
    number: "04",
    title: "沉淀能力",
    copy: "保存为策略或 Skill，在下一次对话中继续复用和改进。",
    icon: Bot,
  },
] as const;

function DemoResult({ demoKey }: { demoKey: (typeof demos)[number]["key"] }) {
  if (demoKey === "research") {
    return (
      <div className="landing-result-visual landing-market-visual" aria-hidden="true">
        <div className="landing-quote-line">
          <span />
          <span />
          <span />
        </div>
        <div className="landing-mini-chart">
          <i />
          <i />
          <i />
          <i />
          <i />
          <i />
          <i />
        </div>
        <div className="landing-source-row">
          <span>行情</span>
          <span>研报</span>
          <span>时间口径</span>
        </div>
      </div>
    );
  }

  if (demoKey === "strategy") {
    return (
      <div className="landing-result-visual landing-rule-visual" aria-hidden="true">
        <span>增长质量</span>
        <b>＋</b>
        <span>合理估值</span>
        <b>＋</b>
        <span>趋势确认</span>
      </div>
    );
  }

  if (demoKey === "backtest") {
    return (
      <div className="landing-result-visual landing-backtest-visual" aria-hidden="true">
        <svg viewBox="0 0 360 92" preserveAspectRatio="none">
          <path className="landing-chart-area" d="M0 77 C42 69 47 54 84 59 S130 34 164 47 S219 22 254 31 S303 15 360 8 L360 92 L0 92 Z" />
          <path className="landing-chart-line" d="M0 77 C42 69 47 54 84 59 S130 34 164 47 S219 22 254 31 S303 15 360 8" />
        </svg>
        <div>
          <span>收益</span>
          <span>回撤</span>
          <span>换手</span>
          <span>成本</span>
        </div>
      </div>
    );
  }

  return (
    <div className="landing-result-visual landing-skill-visual" aria-hidden="true">
      <div><CheckCircle2 size={15} /> 识别公司</div>
      <div><CheckCircle2 size={15} /> 并行执行</div>
      <div><Sparkles size={15} /> 汇总呈现</div>
    </div>
  );
}

export default function LandingPage() {
  const [prompt, setPrompt] = useState("");
  const [activeDemo, setActiveDemo] = useState<(typeof demos)[number]["key"]>("research");
  const [submitError, setSubmitError] = useState("");
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const demoTabRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const demo = demos.find((item) => item.key === activeDemo) ?? demos[0];

  const submitPrompt = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setSubmitError("");
    try {
      const submitted = continueToRegistration(
        prompt,
        window.sessionStorage,
        (path) => window.location.assign(path),
      );
      if (!submitted) setSubmitError("请先写下你想研究的问题或策略。");
    } catch {
      setSubmitError("暂时无法保存当前问题，请稍后重试。");
    }
  };

  const handleInputKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
      event.preventDefault();
      event.currentTarget.form?.requestSubmit();
    }
  };

  const chooseSuggestion = (suggestion: string) => {
    setPrompt(suggestion);
    setSubmitError("");
    window.requestAnimationFrame(() => inputRef.current?.focus());
  };

  const handleDemoKeyDown = (event: KeyboardEvent<HTMLButtonElement>, currentIndex: number) => {
    let nextIndex: number | null = null;
    if (event.key === "ArrowRight") nextIndex = (currentIndex + 1) % demos.length;
    if (event.key === "ArrowLeft") nextIndex = (currentIndex - 1 + demos.length) % demos.length;
    if (event.key === "Home") nextIndex = 0;
    if (event.key === "End") nextIndex = demos.length - 1;
    if (nextIndex === null) return;

    event.preventDefault();
    setActiveDemo(demos[nextIndex].key);
    window.requestAnimationFrame(() => demoTabRefs.current[nextIndex]?.focus());
  };

  return (
    <div className="landing-page">
      <a className="landing-skip-link" href="#landing-main">跳到主要内容</a>

      <header className="landing-header">
        <a className="landing-brand" href="/" aria-label="Fin Agent 首页">
          <span className="landing-brand-mark" aria-hidden="true">
            <Activity size={20} strokeWidth={1.9} />
          </span>
          <span>
            <strong>Fin Agent</strong>
            <small>Financial Intelligence</small>
          </span>
        </a>

        <nav className="landing-nav" aria-label="主页导航">
          <a href="#capabilities">产品能力</a>
          <a href="#workflow">研究闭环</a>
          <a href="#trust">可信设计</a>
        </nav>

        <div className="landing-header-actions">
          <a className="landing-login-link" href="/login">登录</a>
          <a className="landing-register-link" href="/register">
            免费注册
            <ArrowRight size={16} aria-hidden="true" />
          </a>
        </div>
      </header>

      <main id="landing-main">
        <section className="landing-hero" aria-labelledby="landing-hero-title">
          <div className="landing-hero-copy">
            <p className="landing-eyebrow">
              <span aria-hidden="true" />
              AI-NATIVE FINANCIAL WORKSPACE
            </p>
            <h1 id="landing-hero-title">
              从市场问题出发，
              <span>让 Agent 推进研究。</span>
            </h1>
            <p className="landing-hero-description">
              从行情、财务与研报，到策略构建、组合回测与个人 Skill。
              你表达目标，Fin Agent 负责组织数据、工具和过程。
            </p>

            <form className="landing-composer" onSubmit={submitPrompt} aria-label="开始使用 Fin Agent">
              <label className="landing-sr-only" htmlFor="landing-prompt">
                描述你的金融问题或策略
              </label>
              <textarea
                ref={inputRef}
                id="landing-prompt"
                rows={3}
                value={prompt}
                onChange={(event) => {
                  setPrompt(event.target.value);
                  if (submitError) setSubmitError("");
                }}
                onKeyDown={handleInputKeyDown}
                placeholder="问一家公司，描述一个策略，或上传持仓开始……"
                aria-describedby={`landing-prompt-hint${submitError ? " landing-prompt-error" : ""}`}
              />
              <div className="landing-composer-toolbar">
                <span className="landing-attachment-note">
                  <Upload size={17} aria-hidden="true" />
                  <span>注册后可上传文件</span>
                </span>
                <button className="landing-send-button" type="submit" disabled={!prompt.trim()}>
                  开始
                  <Send size={16} aria-hidden="true" />
                </button>
              </div>
              <p id="landing-prompt-hint" className="landing-composer-hint">
                按 <kbd>Ctrl</kbd>/<kbd>⌘</kbd> + <kbd>Enter</kbd> 提交；注册后将从当前问题继续。
              </p>
              {submitError ? (
                <p id="landing-prompt-error" className="landing-form-error" role="alert">
                  {submitError}
                </p>
              ) : null}
            </form>

            <div className="landing-suggestions" aria-label="示例问题">
              {suggestions.map((suggestion) => (
                <button key={suggestion} type="button" onClick={() => chooseSuggestion(suggestion)}>
                  <span>{suggestion}</span>
                  <ArrowRight size={14} aria-hidden="true" />
                </button>
              ))}
            </div>
          </div>

          <div id="product-demo" className="landing-demo-shell" aria-label="Fin Agent 产品交互示例">
            <div className="landing-demo-window">
              <div className="landing-demo-topbar">
                <span className="landing-window-dots" aria-hidden="true"><i /><i /><i /></span>
                <span>FIN AGENT · INTELLIGENCE CONSOLE</span>
                <small><i className="landing-live-dot" /> Agent Online</small>
              </div>

              <div className="landing-demo-tabs" role="tablist" aria-label="选择演示能力">
                {demos.map((item, index) => (
                  <button
                    key={item.key}
                    ref={(node) => {
                      demoTabRefs.current[index] = node;
                    }}
                    id={`landing-demo-tab-${item.key}`}
                    type="button"
                    role="tab"
                    aria-selected={activeDemo === item.key}
                    aria-controls={`landing-demo-panel-${item.key}`}
                    tabIndex={activeDemo === item.key ? 0 : -1}
                    onClick={() => setActiveDemo(item.key)}
                    onKeyDown={(event) => handleDemoKeyDown(event, index)}
                  >
                    {item.label}
                  </button>
                ))}
              </div>

              <div
                id={`landing-demo-panel-${demo.key}`}
                className="landing-demo-panel"
                role="tabpanel"
                aria-labelledby={`landing-demo-tab-${demo.key}`}
                tabIndex={0}
              >
                <div className="landing-demo-question">
                  <span>你</span>
                  <p>{demo.prompt}</p>
                </div>

                <div className="landing-demo-agent">
                  <div className="landing-demo-agent-head">
                    <span className="landing-agent-avatar" aria-hidden="true"><Activity size={15} /></span>
                    <div>
                      <strong>Fin Agent</strong>
                      <small>{demo.eyebrow}</small>
                    </div>
                  </div>

                  <ol className="landing-demo-steps" aria-label={`${demo.label}处理步骤`}>
                    {demo.steps.map((step, index) => (
                      <li key={step}>
                        <span>{String(index + 1).padStart(2, "0")}</span>
                        <p>{step}</p>
                        <CheckCircle2 size={16} aria-hidden="true" />
                      </li>
                    ))}
                  </ol>

                  <article className="landing-demo-result">
                    <div>
                      <small>示例结果</small>
                      <h2>{demo.resultTitle}</h2>
                      <p>{demo.resultCopy}</p>
                    </div>
                    <DemoResult demoKey={demo.key} />
                    <footer>
                      {demo.resultTags.map((tag) => <span key={tag}>{tag}</span>)}
                    </footer>
                  </article>
                </div>
              </div>
            </div>
            <p className="landing-demo-caption">
              过程展示有用的业务线索，正式结果保留时间、来源与关键假设。
            </p>
          </div>
        </section>

        <section className="landing-proof-strip" aria-label="Fin Agent 的可信承诺">
          <div>
            <Database size={19} aria-hidden="true" />
            <span><strong>数据有时点</strong>知道答案基于什么时候</span>
          </div>
          <div>
            <FileSearch size={19} aria-hidden="true" />
            <span><strong>结论有依据</strong>重要信息可以继续核查</span>
          </div>
          <div>
            <ShieldCheck size={19} aria-hidden="true" />
            <span><strong>回测可复查</strong>假设、成本与过程不隐藏</span>
          </div>
        </section>

        <section id="workflow" className="landing-section landing-workflow" aria-labelledby="landing-workflow-title">
          <div className="landing-section-heading">
            <p>一条连续的研究主线</p>
            <h2 id="landing-workflow-title">从问题，到可复用的研究能力</h2>
            <span>
              不是四个孤立入口。一次对话中的事实、策略和验证结果能够自然衔接，
              再沉淀为以后继续使用的能力。
            </span>
          </div>

          <div className="landing-workflow-grid">
            {workflow.map((item, index) => {
              const Icon = item.icon;
              return (
                <article key={item.number}>
                  <div className="landing-workflow-card">
                    <header>
                      <span>{item.number}</span>
                      <Icon size={21} aria-hidden="true" />
                    </header>
                    <h3>{item.title}</h3>
                    <p>{item.copy}</p>
                  </div>
                  {index < workflow.length - 1 ? (
                    <ArrowRight className="landing-workflow-arrow" size={20} aria-hidden="true" />
                  ) : null}
                </article>
              );
            })}
          </div>
        </section>

        <section id="capabilities" className="landing-section landing-capabilities" aria-labelledby="landing-capabilities-title">
          <div className="landing-section-heading landing-section-heading-left">
            <p>四项核心能力</p>
            <h2 id="landing-capabilities-title">自然开始，严谨推进</h2>
            <span>每项能力都服务于真实金融工作，而不是为了展示模型或工具数量。</span>
          </div>

          <div className="landing-capability-grid">
            {capabilities.map((capability) => {
              const Icon = capability.icon;
              return (
                <article key={capability.number} className={`landing-capability-card ${capability.tone}`}>
                  <header>
                    <span>{capability.number}</span>
                    <Icon size={25} strokeWidth={1.7} aria-hidden="true" />
                  </header>
                  <h3>{capability.title}</h3>
                  <p>{capability.description}</p>
                  <ul>
                    {capability.proof.map((item) => (
                      <li key={item}><CheckCircle2 size={15} aria-hidden="true" />{item}</li>
                    ))}
                  </ul>
                </article>
              );
            })}
          </div>
        </section>

        <section id="trust" className="landing-section landing-trust" aria-labelledby="landing-trust-title">
          <div className="landing-trust-copy">
            <p className="landing-eyebrow">
              <span aria-hidden="true" />
              为金融场景设计
            </p>
            <h2 id="landing-trust-title">回答要清楚，更要站得住。</h2>
            <p>
              金融问题中的日期、数据口径、执行假设和缺失信息都会影响结论。
              Fin Agent 把这些关键事实留在结果旁边，让用户能够理解、追问和修订。
            </p>
            <a href="/register">
              从第一个问题开始
              <ArrowRight size={16} aria-hidden="true" />
            </a>
          </div>

          <div className="landing-trust-board" aria-label="可信结果包含的信息">
            <div className="landing-trust-board-head">
              <span><ShieldCheck size={17} aria-hidden="true" />结果证据</span>
              <small>随业务结果呈现</small>
            </div>
            <dl>
              <div><dt>数据时点</dt><dd>交易日与更新时间明确</dd></div>
              <div><dt>查询范围</dt><dd>证券、指标和时间段可见</dd></div>
              <div><dt>策略假设</dt><dd>调仓、费用和约束可复查</dd></div>
              <div><dt>过程线索</dt><dd>有用进展与异常及时反馈</dd></div>
            </dl>
          </div>
        </section>

        <section className="landing-final-cta" aria-labelledby="landing-cta-title">
          <Activity size={24} aria-hidden="true" />
          <h2 id="landing-cta-title">从第一个金融问题开始。</h2>
          <p>不必先学习复杂菜单。说清楚你想了解什么，Fin Agent 会和你一起推进。</p>
          <a href="/register">
            免费注册
            <ArrowRight size={17} aria-hidden="true" />
          </a>
        </section>
      </main>

      <footer className="landing-footer">
        <a className="landing-brand landing-footer-brand" href="/" aria-label="Fin Agent 首页">
          <span className="landing-brand-mark" aria-hidden="true"><Activity size={17} /></span>
          <strong>Fin Agent</strong>
        </a>
        <p>金融数据查询、策略研究与个人 Skill 工作台</p>
        <small>页面示例不构成投资建议；投资决策请结合自身情况独立判断。</small>
      </footer>
    </div>
  );
}
