import { mountWorkbench } from "./app.js";
import { loadReviewWorkbenchData } from "./data/loadReviewData.js";
import { createInitialState } from "./state.js";

const app = document.getElementById("app");
const selectionMenu = document.getElementById("selection-menu");
const toast = document.getElementById("toast");

function renderLoading() {
  app.innerHTML = `
    <main class="workspace workspace--boot">
      <section class="panel panel--boot">
        <div class="panel-shell panel-shell--boot">
          <div class="boot-state">
            <div class="panel-eyebrow">Booting / Review Workbench</div>
            <h1 class="panel-title">正在加载真实样本与 MAS 评估数据</h1>
            <p class="panel-summary">
              当前前端会自动扫描 <code>artifacts/{task}/{sample_name}/{dim}</code>，并尝试关联
              <code>data/training/{task}/{sample_name}.md</code> 进行展示。
            </p>
          </div>
        </div>
      </section>
    </main>
  `;
}

function renderError(error) {
  app.innerHTML = `
    <main class="workspace workspace--boot">
      <section class="panel panel--boot">
        <div class="panel-shell panel-shell--boot">
          <div class="boot-state boot-state--error">
            <div class="panel-eyebrow">Load Failed</div>
            <h1 class="panel-title">前端数据加载失败</h1>
            <p class="panel-summary">${error.message}</p>
            <p class="panel-summary">
              请从仓库根目录启动静态服务，并确保浏览器能访问目录索引，然后再打开 <code>/frontend/index.html</code>。
            </p>
          </div>
        </div>
      </section>
    </main>
  `;
}

async function bootstrap() {
  renderLoading();
  try {
    const data = await loadReviewWorkbenchData();
    const state = createInitialState(data);
    mountWorkbench({
      root: app,
      selectionMenu,
      toastNode: toast,
      state,
    });
  } catch (error) {
    renderError(error);
    throw error;
  }
}

bootstrap();
