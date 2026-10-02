function copyViaCommand(fill: (event: ClipboardEvent) => void) {
  const onCopy = (event: ClipboardEvent) => {
    event.preventDefault();
    fill(event);
  };
  const holder = document.createElement("textarea");
  holder.value = " ";
  holder.setAttribute("readonly", "");
  holder.style.position = "fixed";
  holder.style.left = "0";
  holder.style.top = "0";
  holder.style.opacity = "0";
  holder.style.pointerEvents = "none";
  document.body.appendChild(holder);
  holder.focus();
  holder.select();
  document.addEventListener("copy", onCopy);
  let ok = false;
  try {
    ok = document.execCommand("copy");
  } finally {
    document.removeEventListener("copy", onCopy);
    holder.remove();
  }
  if (!ok) throw new Error("Не удалось скопировать");
}

export async function copyExcelMarkup(html: string, plain: string) {
  if (window.isSecureContext && navigator.clipboard?.write && typeof ClipboardItem !== "undefined") {
    try {
      await navigator.clipboard.write([
        new ClipboardItem({
          "text/html": new Blob([html], { type: "text/html" }),
          "text/plain": new Blob([plain], { type: "text/plain" }),
        }),
      ]);
      return;
    } catch {
      /* по HTTP в локальной сети Clipboard API недоступен */
    }
  }
  copyViaCommand((event) => {
    event.clipboardData?.setData("text/html", html);
    event.clipboardData?.setData("text/plain", plain);
  });
}

export function blobToDataUrl(blob: Blob) {
  return new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(reader.error ?? new Error("Не удалось прочитать картинку"));
    reader.readAsDataURL(blob);
  });
}

/** Синхронно, в момент клика: так же, как таблица, работает по HTTP в локальной сети. */
export function copyImageBlobSync(blob: Blob, dataUrl: string) {
  const html = `<img src="${dataUrl}" alt="График" />`;
  copyViaCommand((event) => {
    const data = event.clipboardData;
    if (!data) return;
    try {
      data.items.add(new File([blob], "grafik.png", { type: "image/png" }));
    } catch {
      /* не все браузеры принимают файл в событии copy */
    }
    data.setData("text/html", html);
    data.setData("text/plain", "График");
  });
}
