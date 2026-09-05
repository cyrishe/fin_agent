import { Check, Copy, TerminalSquare } from "lucide-react";
import { useState } from "react";

export default function CodeBlock({ code, language = "text" }: { code: string; language?: string }) {
  const [copied, setCopied] = useState(false);
  const lines = code.split("\n");
  const copy = async () => {
    await navigator.clipboard.writeText(code);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1200);
  };
  return (
    <div className="code-shell">
      <div className="code-toolbar">
        <span><TerminalSquare size={15} />{language}</span>
        <button type="button" onClick={() => void copy()}>{copied ? <Check size={15} /> : <Copy size={15} />}{copied ? "已复制" : "复制"}</button>
      </div>
      <pre className="code-lines"><code>{lines.map((line, index) => <span className="code-line" data-line={index + 1} key={index}>{line || " "}</span>)}</code></pre>
    </div>
  );
}
