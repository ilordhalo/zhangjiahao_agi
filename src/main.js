const domainData = [
  {
    title: "长周期智能体",
    category: "agent",
    score: "5/5",
    evidence: "强证据",
    summary:
      "模型开始围绕真实工具环境持续行动：读写文件、运行代码、浏览网页、规划任务，并在失败后修正路线。",
    tags: ["电脑使用", "编码代理", "研究工作流"],
  },
  {
    title: "推理时扩展",
    category: "agent",
    score: "5/5",
    evidence: "强证据",
    summary:
      "DeepSeek-R1、Qwen3、Deep Think 与 GPT-5.x 路线共同指向可调思考预算、搜索、验证和自我纠错。",
    tags: ["RL 推理", "验证循环", "思考预算"],
  },
  {
    title: "世界模型",
    category: "embodied",
    score: "4/5",
    evidence: "中强证据",
    summary:
      "Genie 3、Sora 类世界模拟器和多模态视频系统把生成从内容外观推进到可操控、可预测的环境状态。",
    tags: ["交互世界", "时空一致性", "行动后果"],
  },
  {
    title: "具身智能",
    category: "embodied",
    score: "4/5",
    evidence: "中强证据",
    summary:
      "Gemini Robotics、GR00T N1、π0 和 Helix 让模型面对真实物体、传感噪声、连续控制和安全约束。",
    tags: ["机器人", "VLA", "现实反馈"],
  },
  {
    title: "科学发现",
    category: "agent",
    score: "4/5",
    evidence: "中证据",
    summary:
      "AlphaEvolve、AlphaFold 3 与数学证明系统说明 AI 能在可验证领域提出候选解并迭代优化。",
    tags: ["算法发现", "形式化推理", "可验证目标"],
  },
  {
    title: "新评测体系",
    category: "evals",
    score: "4/5",
    evidence: "中强证据",
    summary:
      "ARC-AGI、Humanity's Last Exam、METR、OSWorld 与 Terminal-Bench 把关注点转向泛化、长任务和真实操作。",
    tags: ["ARC-AGI", "METR", "长任务"],
  },
  {
    title: "开放模型扩散",
    category: "infra",
    score: "3/5",
    evidence: "中证据",
    summary:
      "DeepSeek-R1、Qwen3、Llama 4 降低复现和应用门槛，是 AGI 相关能力扩散的基础设施变量。",
    tags: ["开放权重", "蒸馏", "生态扩散"],
  },
  {
    title: "推理基础设施",
    category: "infra",
    score: "3/5",
    evidence: "强证据",
    summary:
      "Stargate、Ironwood TPU、Rubin/Vera Rubin 等建设让长周期智能体和推理时扩展具备规模化运行条件。",
    tags: ["AI 工厂", "推理算力", "成本曲线"],
  },
  {
    title: "安全评估",
    category: "evals",
    score: "3/5",
    evidence: "中强证据",
    summary:
      "模型卡、AISI 评估和红队结果显示能力已经触及网络安全、生物化学和越权行动等现实风险边界。",
    tags: ["安全评估", "红队", "部署边界"],
  },
];

const signalData = [
  {
    label: "长任务时间视野",
    title: "METR 曲线是否继续延长",
    body: "关注模型能否稳定完成多小时到多天的软件和研究任务，而不是只完成精心演示。"
  },
  {
    label: "第三方复现",
    title: "ARC-AGI、HLE、OSWorld 是否出现独立跃升",
    body: "供应商发布成绩需要和第三方、私有集、真实环境复核一起看，避免营销噪声。"
  },
  {
    label: "企业工作流",
    title: "智能体事故率能否下降",
    body: "权限、数据删除、提示注入、目标漂移和隐含用户意图，是进入生产环境前的关键关口。"
  },
  {
    label: "现实迁移",
    title: "世界模型与机器人是否跨场景泛化",
    body: "观察从视频演示走向可测预测、规划、机器人迁移和公开安全评测的速度。"
  },
  {
    label: "安全透明度",
    title: "模型卡是否覆盖长期策略行为",
    body: "更强模型需要披露评测意识、沙袋化、越权行动和长期策略行为，而不只是内容拒答。"
  },
];

const filterLabels = {
  all: "全部 AGI 相关方向",
  agent: "智能体与推理方向",
  embodied: "世界模型与具身方向",
  evals: "评测与安全方向",
  infra: "基础设施与扩散方向",
};

const domainGrid = document.querySelector("#domainGrid");
const filterStatus = document.querySelector("#filterStatus");
const filterButtons = Array.from(document.querySelectorAll("[data-filter]"));
const signalList = document.querySelector("#signalList");

function renderDomains(filter = "all") {
  const visible = domainData.filter((item) => filter === "all" || item.category === filter);

  domainGrid.innerHTML = visible
    .map(
      (item) => `
        <article class="domain-card reveal" data-category="${item.category}" tabindex="0">
          <div class="domain-meta">
            <span>${item.evidence}</span>
            <span class="domain-score">AGI 相关性 ${item.score}</span>
          </div>
          <h3>${item.title}</h3>
          <p>${item.summary}</p>
          <ul class="tag-list" aria-label="${item.title} 关键词">
            ${item.tags.map((tag) => `<li>${tag}</li>`).join("")}
          </ul>
        </article>
      `,
    )
    .join("");

  filterStatus.textContent = `显示 ${filterLabels[filter]}：${visible.length} 个方向。`;
  observeRevealItems();
}

function renderSignals() {
  signalList.innerHTML = signalData
    .map(
      (item) => `
        <article class="signal-item reveal">
          <div class="signal-kicker">${item.label}</div>
          <div>
            <h3>${item.title}</h3>
            <p>${item.body}</p>
          </div>
        </article>
      `,
    )
    .join("");
}

function setFilter(nextFilter) {
  filterButtons.forEach((button) => {
    const active = button.dataset.filter === nextFilter;
    button.setAttribute("aria-pressed", String(active));
  });
  renderDomains(nextFilter);
}

filterButtons.forEach((button) => {
  button.addEventListener("click", () => {
    setFilter(button.dataset.filter);
  });
});

let revealObserver;

function observeRevealItems() {
  const revealItems = document.querySelectorAll(".reveal");
  if (!("IntersectionObserver" in window)) {
    revealItems.forEach((item) => item.classList.add("is-visible"));
    return;
  }

  if (!revealObserver) {
    revealObserver = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            entry.target.classList.add("is-visible");
            revealObserver.unobserve(entry.target);
          }
        });
      },
      { threshold: 0.18 },
    );
  }

  revealItems.forEach((item) => revealObserver.observe(item));
}

function setupCanvas() {
  const canvas = document.querySelector("#intelligenceMap");
  if (!canvas) return;

  const context = canvas.getContext("2d");
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const colors = ["#187b52", "#2458d3", "#ba7a12", "#b43d35", "#6c4fc6"];
  let width = 0;
  let height = 0;
  let nodes = [];

  function resize() {
    const pixelRatio = window.devicePixelRatio || 1;
    width = canvas.clientWidth;
    height = canvas.clientHeight;
    canvas.width = Math.floor(width * pixelRatio);
    canvas.height = Math.floor(height * pixelRatio);
    context.setTransform(pixelRatio, 0, 0, pixelRatio, 0, 0);
    nodes = Array.from({ length: width < 700 ? 34 : 58 }, (_, index) => ({
      x: Math.random() * width,
      y: Math.random() * height,
      vx: (Math.random() - 0.5) * 0.28,
      vy: (Math.random() - 0.5) * 0.28,
      r: 2 + Math.random() * 3,
      color: colors[index % colors.length],
    }));
  }

  function draw(timestamp = 0) {
    context.clearRect(0, 0, width, height);
    context.fillStyle = "#f7f8f3";
    context.fillRect(0, 0, width, height);

    context.strokeStyle = "rgba(17, 20, 17, 0.07)";
    context.lineWidth = 1;
    for (let x = 0; x < width; x += 54) {
      context.beginPath();
      context.moveTo(x, 0);
      context.lineTo(x, height);
      context.stroke();
    }
    for (let y = 0; y < height; y += 54) {
      context.beginPath();
      context.moveTo(0, y);
      context.lineTo(width, y);
      context.stroke();
    }

    nodes.forEach((node, index) => {
      if (!reduceMotion) {
        node.x += node.vx + Math.sin(timestamp / 1400 + index) * 0.05;
        node.y += node.vy + Math.cos(timestamp / 1600 + index) * 0.05;
        if (node.x < 0 || node.x > width) node.vx *= -1;
        if (node.y < 0 || node.y > height) node.vy *= -1;
      }

      nodes.slice(index + 1).forEach((other) => {
        const dx = node.x - other.x;
        const dy = node.y - other.y;
        const distance = Math.hypot(dx, dy);
        if (distance < 142) {
          context.strokeStyle = `rgba(17, 20, 17, ${0.12 - distance / 1500})`;
          context.beginPath();
          context.moveTo(node.x, node.y);
          context.lineTo(other.x, other.y);
          context.stroke();
        }
      });

      context.fillStyle = node.color;
      context.beginPath();
      context.arc(node.x, node.y, node.r, 0, Math.PI * 2);
      context.fill();
    });

    if (!reduceMotion) requestAnimationFrame(draw);
  }

  resize();
  draw();
  window.addEventListener("resize", resize);
}

renderSignals();
renderDomains();
observeRevealItems();
setupCanvas();
