import React, { useEffect, useMemo, useRef, useState } from "react";
import ReactDOM from "react-dom/client";
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import "./styles.css";

type Asset = {
  name: string;
  fileName: string;
  path: string;
  thumb: string;
  width: number;
  height: number;
};

type Resources = {
  baseDir: string;
  items: Asset[];
  regions: Asset[];
};

type Task = {
  begin: string;
  end: string;
  beginName: string;
  endName: string;
  item: string;
  itemName: string;
  liquidName?: string;
  containerName?: string;
  times: number;
  liquidMode: boolean;
};

type LogEntry = {
  id: number;
  line: string;
};

type TaskStatus = {
  running: boolean;
  code?: number | null;
};

type SavedQueue = {
  name: string;
  tasks: Task[];
};

const resolutions = ["自动检测", "1920x1080", "2560x1440", "3840x2160"];
const formats = ["png", "jpg", "webp"];

async function bridge<T>(command: string, payload: unknown = {}): Promise<T> {
  return invoke<T>("bridge", { input: { command, payload } });
}

function shortcutForTauri(value: string) {
  return value
    .trim()
    .replace(/\s+/g, "")
    .split("+")
    .filter(Boolean)
    .map((part) => part.toLowerCase() === "ctrl" ? "CommandOrControl" : part)
    .join("+");
}

function hotkeyFromEvent(event: React.KeyboardEvent<HTMLInputElement>) {
  event.preventDefault();
  event.stopPropagation();
  const key = event.key;
  if (["Control", "Shift", "Alt", "Meta"].includes(key)) return "";
  const parts: string[] = [];
  if (event.ctrlKey) parts.push("Ctrl");
  if (event.altKey) parts.push("Alt");
  if (event.shiftKey) parts.push("Shift");
  if (event.metaKey) parts.push("Meta");

  const aliases: Record<string, string> = {
    " ": "Space",
    Escape: "Escape",
    Esc: "Escape",
    ArrowUp: "ArrowUp",
    ArrowDown: "ArrowDown",
    ArrowLeft: "ArrowLeft",
    ArrowRight: "ArrowRight",
  };
  let normalized = aliases[key] ?? key;
  if (normalized.length === 1) normalized = normalized.toUpperCase();
  parts.push(normalized);
  return parts.join("+");
}

function App() {
  const [page, setPage] = useState<"transport" | "editor">("transport");
  const [resources, setResources] = useState<Resources>({ baseDir: "", items: [], regions: [] });
  const [selectedItem, setSelectedItem] = useState<Asset | null>(null);
  const [begin, setBegin] = useState("");
  const [end, setEnd] = useState("");
  const [times, setTimes] = useState(30);
  const [resolution, setResolution] = useState("自动检测");
  const [hotkey, setHotkey] = useState("F8");
  const [liquidMode, setLiquidMode] = useState(false);
  const [liquid, setLiquid] = useState<Asset | null>(null);
  const [container, setContainer] = useState<Asset | null>(null);
  const [queue, setQueue] = useState<Task[]>([]);
  const [savedQueues, setSavedQueues] = useState<SavedQueue[]>([]);
  const [showLoadCard, setShowLoadCard] = useState(false);
  const [running, setRunning] = useState(false);
  const [logs, setLogs] = useState<string[]>(["准备就绪。"]);
  const seenLogIds = useRef<Set<number>>(new Set());
  const [editing, setEditing] = useState<Asset | null>(null);

  const logBox = useRef<HTMLDivElement>(null);
  const stickToLogBottom = useRef(true);

  const appendBufferedLogs = async () => {
    const buffered = await invoke<LogEntry[]>("transport_logs").catch(() => []);
    setLogs((old) => {
      const next = [...old];
      for (const entry of buffered) {
        if (seenLogIds.current.has(entry.id)) continue;
        seenLogIds.current.add(entry.id);
        next.push(entry.line);
      }
      return next;
    });
  };

  const refresh = async () => {
    const next = await bridge<Resources>("resources");
    setResources(next);
    setSelectedItem((old) => next.items.find((item) => item.path === old?.path) ?? next.items[0] ?? null);
    setBegin((old) => next.regions.some((r) => r.path === old) ? old : next.regions[0]?.path ?? "");
    setEnd((old) => {
      if (next.regions.some((r) => r.path === old)) return old;
      return next.regions[1]?.path ?? next.regions[0]?.path ?? "";
    });
    setLogs((old) => [...old, "已刷新 item / region 资源。"]);
  };

  useEffect(() => {
    refresh().catch((e) => setLogs((old) => [...old, `资源加载失败: ${e}`]));
    try {
      const stored = localStorage.getItem("zmdstore_saved_queues");
      if (stored) {
        const parsed = JSON.parse(stored);
        if (parsed.length > 0 && Array.isArray(parsed[0])) {
          const migrated = parsed.map((tasks: Task[], i: number) => ({ name: `队列组 ${i + 1}`, tasks }));
          setSavedQueues(migrated);
          localStorage.setItem("zmdstore_saved_queues", JSON.stringify(migrated));
        } else {
          setSavedQueues(parsed);
        }
      }
    } catch (e) {}

    const disposers: Array<() => void> = [];
    listen<LogEntry>("transport-log", (event) => {
      const entry = event.payload;
      if (seenLogIds.current.has(entry.id)) return;
      seenLogIds.current.add(entry.id);
      setLogs((old) => [...old, entry.line]);
    }).then((d) => disposers.push(d));
    listen("transport-finished", async (event) => {
      await appendBufferedLogs();
      setRunning(false);
      invoke("unregister_hotkey").catch(() => undefined);
      const payload = event.payload as { code?: number | null };
      setLogs((old) => [...old, payload.code === 0 ? "搬运任务已完成。" : "搬运任务已停止或异常退出。"]);
    }).then((d) => disposers.push(d));
    return () => disposers.forEach((dispose) => dispose());
  }, []);

  useEffect(() => {
    if (!running) return;
    const id = window.setInterval(() => {
      appendBufferedLogs();
      invoke<TaskStatus>("transport_status").then((status) => {
        if (!status.running) {
          setRunning(false);
          invoke("unregister_hotkey").catch(() => undefined);
        }
      }).catch(() => undefined);
    }, 500);
    return () => window.clearInterval(id);
  }, [running]);

  useEffect(() => {
    if (!stickToLogBottom.current) return;
    logBox.current?.scrollTo({ top: logBox.current.scrollHeight });
  }, [logs]);

  const regionByPath = useMemo(() => new Map(resources.regions.map((r) => [r.path, r])), [resources.regions]);

  const currentTask = async (): Promise<Task | null> => {
    const beginAsset = regionByPath.get(begin);
    const endAsset = regionByPath.get(end);
    if (!beginAsset || !endAsset) {
      setLogs((old) => [...old, "请先准备至少两个仓库区域模板。"]);
      return null;
    }
    if (begin === end) {
      setLogs((old) => [...old, "起点和终点不能相同。"]);
      return null;
    }
    let item = selectedItem?.path ?? "";
    let itemName = selectedItem?.name ?? "";
    let liquidName: string | undefined;
    let containerName: string | undefined;
    if (liquidMode) {
      if (!container) {
        setLogs((old) => [...old, "液体运输模式需要至少设置容器。"]);
        return null;
      }
      item = liquid ? await composeLiquidItem(liquid, container) : container.path;
      itemName = liquid ? "液体合成" : container.name;
      liquidName = liquid?.name;
      containerName = container.name;
    } else if (!selectedItem) {
      setLogs((old) => [...old, "请先选择一个需要搬运的物品。"]);
      return null;
    }
    return {
      begin,
      end,
      beginName: beginAsset.name,
      endName: endAsset.name,
      item,
      itemName,
      liquidName,
      containerName,
      times,
      liquidMode,
    };
  };

  const loadImageElement = (dataUrl: string) => new Promise<HTMLImageElement>((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = reject;
    img.src = dataUrl;
  });

  const composeLiquidItem = async (liquidAsset: Asset, containerAsset: Asset) => {
    const [liquidData, containerData] = await Promise.all([
      invoke<string>("image_data_direct", { payload: { path: liquidAsset.path } }),
      invoke<string>("image_data_direct", { payload: { path: containerAsset.path } }),
    ]);
    const [liquidImg, containerImg] = await Promise.all([
      loadImageElement(liquidData),
      loadImageElement(containerData),
    ]);
    const canvas = document.createElement("canvas");
    const ctx = canvas.getContext("2d");
    if (!ctx) throw new Error("鏃犳硶鍒涘缓鐢诲竷");
    canvas.width = containerImg.naturalWidth;
    canvas.height = containerImg.naturalHeight;
    ctx.drawImage(containerImg, 0, 0);
    const scale = 40 / 88;
    const width = Math.max(1, Math.round(liquidImg.naturalWidth * scale));
    const height = Math.max(1, Math.round(liquidImg.naturalHeight * scale));
    ctx.drawImage(liquidImg, Math.round((canvas.width - width) / 2), Math.round((canvas.height - height) / 2), width, height);
    return invoke<string>("save_temp_image_direct", { dataUrl: canvas.toDataURL("image/png") });
  };

  const describeTaskItem = (task: Task) => {
    if (task.liquidMode && task.itemName === "液体合成") {
      return `液体合成: ${task.liquidName ?? "无液体"} + ${task.containerName ?? "未选择容器"}`;
    }
    return task.itemName;
  };

  const selectItem = (item: Asset) => {
    setSelectedItem(item);
    setLogs((old) => [...old, `已选择物品: ${item.name}`]);
  };

  const startTransport = async (batch = false) => {
    try {
      let payload: Record<string, unknown>;
      if (batch) {
        payload = { tasks: queue, resolution };
      } else {
        const task = await currentTask();
        if (!task) return;
        payload = { ...task, resolution };
      }
      if (batch && queue.length === 0) {
        setLogs((old) => [...old, "队列为空，请先添加任务。"]);
        return;
      }
      seenLogIds.current.clear();
      setRunning(true);
      setLogs([batch ? `批量任务启动，共 ${queue.length} 个任务。停止快捷键: ${hotkey}` : `任务开始。停止快捷键: ${hotkey}`]);
      await invoke("register_hotkey", { shortcut: shortcutForTauri(hotkey) || "F8" });
      await invoke("start_transport", { payload });
    } catch (e) {
      setRunning(false);
      setLogs((old) => [...old, `启动失败: ${e}`]);
      await invoke("unregister_hotkey").catch(() => undefined);
    }
  };

  const stopTransport = async () => {
    await invoke("stop_transport").catch(() => undefined);
    await invoke("unregister_hotkey").catch(() => undefined);
    setRunning(false);
    setLogs((old) => [...old, "已发送停止请求。"]);
  };

  const addToQueue = async () => {
    const task = await currentTask();
    if (!task) return;
    setQueue((old) => [...old, task]);
    setLogs((old) => [...old, `已添加到队列: ${task.beginName} -> ${task.endName} (${task.times}次 | ${describeTaskItem(task)})`]);
  };

  const removeQueueTask = (index: number) => {
    setQueue((old) => old.filter((_, i) => i !== index));
    setLogs((old) => [...old, `已删除队列任务 #${index + 1}`]);
  };

  const saveQueue = () => {
    if (queue.length === 0) {
      setLogs((old) => [...old, "当前队列为空，无法保存。"]);
      return;
    }
    const qStr = JSON.stringify(queue);
    if (savedQueues.some((sq) => JSON.stringify(sq.tasks) === qStr)) {
      setLogs((old) => [...old, "该队列组合已存在，无需重复保存。"]);
      return;
    }
    const newName = window.prompt("请输入要保存的队列名称:", `自定义队列 ${savedQueues.length + 1}`);
    if (newName === null) return;
    const next = [...savedQueues, { name: newName.trim() || `自定义队列 ${savedQueues.length + 1}`, tasks: queue }];
    setSavedQueues(next);
    localStorage.setItem("zmdstore_saved_queues", JSON.stringify(next));
    setLogs((old) => [...old, `批量任务队列已保存 (共 ${next.length} 组)。`]);
  };

  const applySavedQueue = (sq: SavedQueue) => {
    setQueue(sq.tasks);
    setShowLoadCard(false);
    setLogs((old) => [...old, `已成功加载保存的批量任务队列 (${sq.tasks.length} 个任务)。`]);
  };

  const removeSavedQueue = (e: React.MouseEvent, index: number) => {
    e.stopPropagation();
    const next = savedQueues.filter((_, i) => i !== index);
    setSavedQueues(next);
    localStorage.setItem("zmdstore_saved_queues", JSON.stringify(next));
    setLogs((old) => [...old, `已删除保存的队列组 #${index + 1}`]);
  };

  const renameSavedQueue = (e: React.MouseEvent, index: number) => {
    e.stopPropagation();
    const newName = window.prompt("请输入新的队列名称:", savedQueues[index].name);
    if (newName !== null && newName.trim() !== "") {
      const next = [...savedQueues];
      next[index].name = newName.trim();
      setSavedQueues(next);
      localStorage.setItem("zmdstore_saved_queues", JSON.stringify(next));
    }
  };

  const deleteAsset = async (asset: Asset) => {
    if (!confirm(`确定要删除图片 ${asset.fileName} 吗？`)) return;
    await invoke("delete_image_direct", { payload: { path: asset.path } });
    setResources((old) => ({
      ...old,
      items: old.items.filter((item) => item.path !== asset.path),
      regions: old.regions.filter((region) => region.path !== asset.path),
    }));
    setSelectedItem((old) => old?.path === asset.path ? null : old);
  };

  const openEditor = (asset?: Asset) => {
    setEditing(asset ?? null);
    setPage("editor");
  };

  return (
    <main className="shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="mark">Z</span>
          <div>
            <strong>ZMD Store</strong>
            <small>Tauri Console</small>
          </div>
        </div>
        <button className={page === "transport" ? "nav active" : "nav"} onClick={() => setPage("transport")}>运送任务</button>
        <button className={page === "editor" ? "nav active" : "nav"} onClick={() => openEditor()}>素材编辑</button>
        <div className="stat">
          <span>{resources.items.length}</span> item
          <span>{resources.regions.length}</span> region
        </div>
      </aside>

      <section className="workspace">
        {page === "transport" ? (
          <>
            <header className="toolbar">
              <div>
                <h1>运送任务</h1>
                <p>选择起点、终点、次数和物品图片后开始搬运。</p>
              </div>
              <div className="actions">
                <button onClick={refresh}>刷新资源</button>
                <button className="primary" disabled={running} onClick={() => startTransport(false)}>开始搬运</button>
                <button className="danger" disabled={!running} onClick={stopTransport}>停止</button>
              </div>
            </header>

            <div className="transport-grid">
              <section className="panel main-panel">
                <div className="form-grid">
                  <label>起点<select value={begin} onChange={(e) => setBegin(e.target.value)}>{resources.regions.map((r) => <option key={r.path} value={r.path}>{r.name}</option>)}</select></label>
                  <label>终点<select value={end} onChange={(e) => setEnd(e.target.value)}>{resources.regions.map((r) => <option key={r.path} value={r.path}>{r.name}</option>)}</select></label>
                  <label>次数<input type="number" min={1} max={999} value={times} onChange={(e) => setTimes(Number(e.target.value))} /></label>
                  <label>停止快捷键<input value={hotkey} readOnly onKeyDown={(e) => {
                    const next = hotkeyFromEvent(e);
                    if (next) setHotkey(next);
                  }} onFocus={(e) => e.currentTarget.select()} /></label>
                  <label>屏幕分辨率<select value={resolution} onChange={(e) => setResolution(e.target.value)}>{resolutions.map((r) => <option key={r}>{r}</option>)}</select></label>
                </div>

                <div className="mode-row">
                  <h2>选择物品</h2>
                  <label className="check"><input type="checkbox" checked={liquidMode} onChange={(e) => setLiquidMode(e.target.checked)} /> 液体运输模式</label>
                  {liquidMode && (
                    <div className="liquid-box">
                      <button disabled={!selectedItem} onClick={() => setLiquid(selectedItem)}>设为液体</button>
                      <strong>液体: {liquid?.name ?? "未选择"}</strong>
                      <button disabled={!selectedItem} onClick={() => setContainer(selectedItem)}>设为容器</button>
                      <strong>容器: {container?.name ?? "未选择"}</strong>
                    </div>
                  )}
                </div>

                <div className="asset-grid">
                  {resources.items.map((item) => (
                    <article key={item.path} className={selectedItem?.path === item.path ? "asset selected" : "asset"} onClick={() => selectItem(item)}>
                      <img src={item.thumb} alt={item.name} />
                      <span>{item.name}</span>
                      <div className="asset-actions">
                        <button onClick={(e) => { e.stopPropagation(); openEditor(item); }}>编辑</button>
                        <button onClick={(e) => { e.stopPropagation(); deleteAsset(item); }}>删除</button>
                      </div>
                    </article>
                  ))}
                </div>
              </section>

              <section className="panel queue-panel" onClick={() => setShowLoadCard(false)}>
                <h2>批量任务队列</h2>
                <div className="queue-list">
                  {queue.map((task, index) => <div key={`${task.item}-${index}`} className="queue-item"><b>{index + 1}</b><span>{task.beginName} {"->"} {task.endName}</span><small>{task.times}次 | {describeTaskItem(task)}</small><button onClick={() => removeQueueTask(index)}>删除</button></div>)}
                </div>
                <div className="queue-actions">
                  <button onClick={addToQueue}>添加到队列</button>
                  <button onClick={() => setQueue([])}>清空</button>
                  <button onClick={saveQueue}>保存队列</button>
                  <button onClick={(e) => { e.stopPropagation(); setShowLoadCard(!showLoadCard); }}>读取队列</button>
                </div>

                <div className="queue-divider-wrapper">
                  <hr className="queue-divider" />
                  <div className={`load-queue-card ${showLoadCard ? "show" : ""}`} onClick={(e) => e.stopPropagation()}>
                    <div className="load-queue-list">
                      {savedQueues.length === 0 ? (
                        <p className="empty-msg">暂无保存的队列组</p>
                      ) : (
                        savedQueues.map((sq, i) => (
                          <div key={i} className="saved-queue-item" onClick={() => applySavedQueue(sq)}>
                            <div className="saved-queue-info">
                              <strong>{sq.name}</strong>
                              <span>包含 {sq.tasks.length} 个任务</span>
                            </div>
                            <div className="saved-queue-actions">
                              <button onClick={(e) => renameSavedQueue(e, i)}>重命名</button>
                              <button onClick={(e) => removeSavedQueue(e, i)}>删除</button>
                            </div>
                          </div>
                        ))
                      )}
                    </div>
                  </div>
                </div>

                <button className="primary" disabled={running} onClick={() => startTransport(true)} style={{ width: "100%" }}>启动批量任务</button>
              </section>
            </div>

            <section className="log-panel">
              <div><strong>运行日志</strong><button onClick={() => setLogs([])}>清空</button></div>
              <div ref={logBox} className="logs" onScroll={(event) => {
                const target = event.currentTarget;
                stickToLogBottom.current = target.scrollHeight - target.scrollTop - target.clientHeight < 16;
              }}>{logs.map((line, index) => <p key={index}>{line}</p>)}</div>
            </section>
          </>
        ) : (
          <Editor resources={resources} initial={editing} onDone={() => { setPage("transport"); refresh().catch((e) => setLogs((old) => [...old, `璧勬簮鍒锋柊澶辫触: ${e}`])); }} />
        )}
      </section>
    </main>
  );
}

function Editor({ resources, initial, onDone }: { resources: Resources; initial: Asset | null; onDone: () => void }) {
  const [type, setType] = useState<"item" | "region">(initial?.path.replace(/\//g, "\\").includes("\\region\\") ? "region" : "item");
  const [format, setFormat] = useState("png");
  const [name, setName] = useState(initial?.name ?? "");
  const [source, setSource] = useState("");
  const [original, setOriginal] = useState("");
  const [preview, setPreview] = useState("");
  const [scale, setScale] = useState(100);
  const [mode, setMode] = useState<"rectangle" | "polygon">("rectangle");
  const [points, setPoints] = useState<Array<[number, number]>>([]);
  const [rect, setRect] = useState({ x: 0, y: 0, w: 0, h: 0 });
  const [size, setSize] = useState({ w: 0, h: 0 });
  const [targetSize, setTargetSize] = useState({ w: "", h: "" });
  const [dragStart, setDragStart] = useState<[number, number] | null>(null);
  const [dragging, setDragging] = useState(false);
  const imgRef = useRef<HTMLImageElement>(null);

  const imageFromDataUrl = (dataUrl: string) => new Promise<HTMLImageElement>((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = reject;
    img.src = dataUrl;
  });

  const canvasToDataUrl = (canvas: HTMLCanvasElement, nextFormat = "png") => (
    canvas.toDataURL(nextFormat === "jpg" ? "image/jpeg" : `image/${nextFormat}`, 0.95)
  );

  const setPreviewImage = async (dataUrl: string) => {
    const img = await imageFromDataUrl(dataUrl);
    setPreview(dataUrl);
    setSize({ w: img.naturalWidth, h: img.naturalHeight });
    setTargetSize({ w: String(img.naturalWidth), h: String(img.naturalHeight) });
    setRect({ x: 0, y: 0, w: 0, h: 0 });
    setPoints([]);
  };

  const loadSource = async (dataUrl: string, nextName?: string) => {
    setSource(dataUrl);
    setOriginal(dataUrl);
    setScale(100);
    await setPreviewImage(dataUrl);
    if (nextName) setName(nextName);
  };

  useEffect(() => {
    if (!initial) return;
    invoke<string>("image_data_direct", { payload: { path: initial.path } }).then((dataUrl) => {
      loadSource(dataUrl, initial.name);
      setType(initial.path.replace(/\//g, "\\").includes("\\region\\") ? "region" : "item");
    });
  }, [initial]);

  const loadFile = (file: File) => {
    const reader = new FileReader();
    reader.onload = () => {
      loadSource(String(reader.result), file.name.replace(/\.[^.]+$/, ""));
    };
    reader.readAsDataURL(file);
  };

  const imagePoint = (event: React.MouseEvent<HTMLElement>) => {
    const img = imgRef.current;
    if (!img || !size.w || !size.h) return [0, 0] as [number, number];
    const box = img.getBoundingClientRect();
    const x = ((event.clientX - box.left) / box.width) * size.w;
    const y = ((event.clientY - box.top) / box.height) * size.h;
    return [
      Math.min(size.w, Math.max(0, Math.round(x))),
      Math.min(size.h, Math.max(0, Math.round(y))),
    ] as [number, number];
  };

  const scalePreview = async (nextScale: number) => {
    setScale(nextScale);
    if (!original) return;
    const img = await imageFromDataUrl(original);
    const canvas = document.createElement("canvas");
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    const factor = nextScale / 100;
    canvas.width = Math.max(1, Math.round(img.naturalWidth * factor));
    canvas.height = Math.max(1, Math.round(img.naturalHeight * factor));
    ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
    await setPreviewImage(canvasToDataUrl(canvas, "png"));
  };

  const applyCrop = async () => {
    if (!preview) {
      alert("请先加载图片。");
      return;
    }
    const img = await imageFromDataUrl(preview);
    const canvas = document.createElement("canvas");
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    if (mode === "rectangle") {
      if (rect.w <= 0 || rect.h <= 0) {
        alert("请先在图片上拖拽选择裁剪区域。");
        return;
      }
      canvas.width = Math.max(1, Math.round(rect.w));
      canvas.height = Math.max(1, Math.round(rect.h));
      ctx.drawImage(img, rect.x, rect.y, rect.w, rect.h, 0, 0, canvas.width, canvas.height);
    } else {
      if (points.length < 3) {
        alert("请拖动画出一个不规则裁剪区域。");
        return;
      }
      const minX = Math.min(...points.map((p) => p[0]));
      const minY = Math.min(...points.map((p) => p[1]));
      const maxX = Math.max(...points.map((p) => p[0]));
      const maxY = Math.max(...points.map((p) => p[1]));
      canvas.width = Math.max(1, Math.round(maxX - minX));
      canvas.height = Math.max(1, Math.round(maxY - minY));
      ctx.save();
      ctx.beginPath();
      points.forEach(([x, y], i) => i === 0 ? ctx.moveTo(x - minX, y - minY) : ctx.lineTo(x - minX, y - minY));
      ctx.closePath();
      ctx.clip();
      ctx.drawImage(img, -minX, -minY);
      ctx.restore();
    }
    const next = canvasToDataUrl(canvas, "png");
    setOriginal(next);
    setScale(100);
    await setPreviewImage(next);
  };

  const applyTargetResolution = async () => {
    if (!preview) {
      alert("请先加载图片。");
      return;
    }
    const w = Number(targetSize.w);
    const h = Number(targetSize.h);
    if (!Number.isInteger(w) || !Number.isInteger(h) || w <= 0 || h <= 0) {
      alert("请输入有效的目标宽高。");
      return;
    }
    const img = await imageFromDataUrl(preview);
    const canvas = document.createElement("canvas");
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    canvas.width = w;
    canvas.height = h;
    ctx.drawImage(img, 0, 0, w, h);
    const next = canvasToDataUrl(canvas, "png");
    setOriginal(next);
    setScale(100);
    await setPreviewImage(next);
  };

  const resetEditor = async () => {
    if (!source) return;
    setOriginal(source);
    setScale(100);
    await setPreviewImage(source);
  };

  const renderToCanvas = async () => {
    const img = imgRef.current;
    if (!img) throw new Error("请先加载图片");
    const factor = scale / 100;
    const canvas = document.createElement("canvas");
    const ctx = canvas.getContext("2d");
    if (!ctx) throw new Error("无法创建画布");
    if (mode === "rectangle") {
      canvas.width = Math.max(1, Math.round(rect.w * factor));
      canvas.height = Math.max(1, Math.round(rect.h * factor));
      ctx.drawImage(img, rect.x, rect.y, rect.w, rect.h, 0, 0, canvas.width, canvas.height);
    } else {
      const usable = points.length >= 3 ? points : [[0, 0], [size.w, 0], [size.w, size.h], [0, size.h]] as Array<[number, number]>;
      const minX = Math.min(...usable.map((p) => p[0]));
      const minY = Math.min(...usable.map((p) => p[1]));
      const maxX = Math.max(...usable.map((p) => p[0]));
      const maxY = Math.max(...usable.map((p) => p[1]));
      canvas.width = Math.max(1, Math.round((maxX - minX) * factor));
      canvas.height = Math.max(1, Math.round((maxY - minY) * factor));
      ctx.save();
      ctx.scale(factor, factor);
      ctx.beginPath();
      usable.forEach(([x, y], i) => i === 0 ? ctx.moveTo(x - minX, y - minY) : ctx.lineTo(x - minX, y - minY));
      ctx.closePath();
      ctx.clip();
      ctx.drawImage(img, -minX, -minY);
      ctx.restore();
    }
    return canvas.toDataURL(format === "jpg" ? "image/jpeg" : `image/${format}`, 0.95);
  };

  const save = async () => {
    try {
      if (!preview) throw new Error("请先加载图片");
      const cleanName = name.trim();
      if (!cleanName) throw new Error("请填写保存文件名");
      const outputName = `${cleanName}.${format}`;
      const existing = (type === "item" ? resources.items : resources.regions).some((asset) => asset.fileName === outputName);
      if (existing && !confirm(`目标路径中已存在同名文件 ${outputName}，是否覆盖？`)) return;

      const img = await imageFromDataUrl(preview);
      const canvas = document.createElement("canvas");
      const ctx = canvas.getContext("2d");
      if (!ctx) throw new Error("无法创建画布");
      canvas.width = img.naturalWidth;
      canvas.height = img.naturalHeight;
      if (format === "jpg") {
        ctx.fillStyle = "#ffffff";
        ctx.fillRect(0, 0, canvas.width, canvas.height);
      }
      ctx.drawImage(img, 0, 0);
      await invoke("save_image_direct", { payload: { dataUrl: canvasToDataUrl(canvas, format), type, name: cleanName, format } });
      onDone();
    } catch (e) {
      alert(e);
    }
  };

  return (
    <section className="panel editor">
      <header className="toolbar compact">
        <div><h1>素材编辑</h1><p>导入、裁剪、缩放并保存 item 或 region 模板。</p></div>
        <button className="primary" onClick={save}>保存图片</button>
      </header>
      <div className="editor-controls">
        <label>类型<select value={type} onChange={(e) => setType(e.target.value as "item" | "region")}><option>item</option><option>region</option></select></label>
        <label>裁剪模式<select value={mode} onChange={(e) => setMode(e.target.value as "rectangle" | "polygon")}><option value="rectangle">矩形裁剪</option><option value="polygon">不规则裁剪</option></select></label>
        <label>格式<select value={format} onChange={(e) => setFormat(e.target.value)}>{formats.map((f) => <option key={f}>{f}</option>)}</select></label>
        <label>文件名<input value={name} onChange={(e) => setName(e.target.value)} /></label>
        <label className="file-button">选择图片<input type="file" accept="image/*" onChange={(e) => e.target.files?.[0] && loadFile(e.target.files[0])} /></label>
      </div>
      <div className="resolution-row">
        <span>当前分辨率: {size.w || "-"} x {size.h || "-"}</span>
        <label>目标宽<input value={targetSize.w} onChange={(e) => setTargetSize({ ...targetSize, w: e.target.value })} /></label>
        <label>目标高<input value={targetSize.h} onChange={(e) => setTargetSize({ ...targetSize, h: e.target.value })} /></label>
        <button onClick={applyTargetResolution}>按分辨率缩放</button>
      </div>
      <div className="scale-row">
        <span>缩放: {scale}%</span>
        <input type="range" min={10} max={200} value={scale} onChange={(e) => scalePreview(Number(e.target.value))} />
        <button onClick={applyCrop}>应用裁剪</button>
        <button onClick={resetEditor}>重置</button>
      </div>
      <div className="crop-stage">
        {preview ? (
          <div
            className="image-wrap"
            onMouseDown={(e) => {
              const point = imagePoint(e);
              setDragging(true);
              setDragStart(point);
              if (mode === "rectangle") {
                setRect({ x: point[0], y: point[1], w: 0, h: 0 });
              } else {
                setPoints([point]);
              }
            }}
            onMouseMove={(e) => {
              if (!dragging || !dragStart) return;
              const point = imagePoint(e);
              if (mode === "rectangle") {
                const x = Math.min(dragStart[0], point[0]);
                const y = Math.min(dragStart[1], point[1]);
                setRect({ x, y, w: Math.abs(point[0] - dragStart[0]), h: Math.abs(point[1] - dragStart[1]) });
              } else {
                setPoints((old) => [...old, point]);
              }
            }}
            onMouseUp={() => { setDragging(false); setDragStart(null); }}
            onMouseLeave={() => { setDragging(false); setDragStart(null); }}
          >
            <img ref={imgRef} src={preview} alt="编辑预览" draggable={false} />
            {mode === "rectangle" && rect.w > 0 && rect.h > 0 ? <div className="rect" style={{ left: `${(rect.x / size.w) * 100}%`, top: `${(rect.y / size.h) * 100}%`, width: `${(rect.w / size.w) * 100}%`, height: `${(rect.h / size.h) * 100}%` }} /> : null}
            {mode === "polygon" && points.length > 1 ? <svg className="polygon-overlay" viewBox={`0 0 ${size.w} ${size.h}`} preserveAspectRatio="none"><polygon points={points.map(([x, y]) => `${x},${y}`).join(" ")} /></svg> : null}
          </div>
        ) : <p className="empty">请选择一张图片。</p>}
      </div>
      <div className="rect-controls">
        <label>X<input type="number" value={rect.x} onChange={(e) => setRect({ ...rect, x: Number(e.target.value) })} /></label>
        <label>Y<input type="number" value={rect.y} onChange={(e) => setRect({ ...rect, y: Number(e.target.value) })} /></label>
        <label>宽<input type="number" value={rect.w} onChange={(e) => setRect({ ...rect, w: Number(e.target.value) })} /></label>
        <label>高<input type="number" value={rect.h} onChange={(e) => setRect({ ...rect, h: Number(e.target.value) })} /></label>
        <span>当前: {size.w} x {size.h}</span>
      </div>
    </section>
  );
}

ReactDOM.createRoot(document.getElementById("root")!).render(<App />);
