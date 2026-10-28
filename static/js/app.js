/* Radar TACHYON — painel (pt-BR, sem build). */
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];

const estado = {
  meta: null,
  offset: 0,
  limit: 100,
  campanhaId: null,
  leadAtual: null,
  idsVisiveis: [],
  pollTimer: null,
};

/* ── Utilidades ─────────────────────────────────────────────────────── */

async function api(path, opts = {}) {
  const r = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  if (!r.ok) {
    let msg = r.statusText;
    try {
      const j = await r.json();
      msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail || j);
    } catch (_) {}
    throw new Error(msg);
  }
  return (r.headers.get("content-type") || "").includes("application/json") ? r.json() : r;
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

function toast(msg, erro = false) {
  const t = document.createElement("div");
  t.className = "toast" + (erro ? " erro" : "");
  t.textContent = msg;
  $("#toasts").appendChild(t);
  setTimeout(() => t.remove(), erro ? 6000 : 3000);
}

const falhar = (e) => toast(e.message || String(e), true);

function fmtData(iso, comHora = true) {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d)) return iso;
  const op = { day: "2-digit", month: "2-digit", year: "2-digit" };
  if (comHora) Object.assign(op, { hour: "2-digit", minute: "2-digit" });
  return d.toLocaleString("pt-BR", op);
}

function nomeStatus(id) {
  return (estado.meta?.status.find((s) => s.id === id) || {}).nome || id || "";
}

function nomeSegmento(id) {
  return (estado.meta?.segmentos.find((s) => s.id === id) || {}).nome || id || "—";
}

function urlExterna(u) {
  if (!u) return "";
  return /^https?:\/\//i.test(u) ? u : "https://" + u;
}

function debounce(fn, ms = 300) {
  let t;
  return (...a) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...a), ms);
  };
}

const quem = () => $("#voce").value || null;

function opcoes(sel, itens, valor = (x) => x, rotulo = (x) => x) {
  itens.forEach((i) => {
    const o = document.createElement("option");
    o.value = valor(i);
    o.textContent = rotulo(i);
    sel.appendChild(o);
  });
}

/* ── Abas ───────────────────────────────────────────────────────────── */

const carregadores = {
  leads: () => Promise.all([carregarLeads(), carregarResumo()]),
  campanhas: () => carregarCampanhas(),
  envios: async () => renderFila(),
  funil: () => carregarFunil(),
  nao: () => carregarNao(),
};

function abrirAba(nome) {
  if (!carregadores[nome]) nome = "leads";
  $$("#tabs button").forEach((b) => b.classList.toggle("ativo", b.dataset.tab === nome));
  $$(".painel").forEach((p) => p.classList.toggle("ativo", p.id === `tab-${nome}`));
  if (nome !== "leads") fecharFicha();
  history.replaceState(null, "", "#" + nome);
  carregadores[nome]().catch(falhar);
}

$$("#tabs button").forEach((b) => b.addEventListener("click", () => abrirAba(b.dataset.tab)));

/* ── Topo ───────────────────────────────────────────────────────────── */

async function carregarSaude() {
  const el = $("#motor");
  try {
    const s = await api("/api/saude");
    let classe = s.motor_scraper ? "ok" : "err";
    let texto = s.motor_scraper ? "Motor online" : "Motor offline";
    if (s.campanha_rodando) {
      classe = "warn";
      texto = "Campanha em andamento";
    }
    el.innerHTML = `<i class="dot ${classe}"></i><span>${texto}</span>`;
    el.title = `${s.motor_url} · geo ${s.geocode_sao_luis.usou_fallback ? "fallback" : "Nominatim"} (${s.geocode_sao_luis.lat}, ${s.geocode_sao_luis.lon})`;
  } catch (e) {
    el.innerHTML = `<i class="dot err"></i><span>Painel sem resposta</span>`;
  }
}

$("#voce").addEventListener("change", () => {
  try {
    localStorage.setItem("radar.voce", $("#voce").value);
  } catch (_) {}
});

/* ── Leads ──────────────────────────────────────────────────────────── */

function filtros() {
  const p = new URLSearchParams();
  const set = (k, v) => v !== "" && v != null && p.set(k, v);
  set("q", $("#f-q").value.trim());
  set("segmento_id", $("#f-segmento").value);
  set("status", $("#f-status").value);
  set("bairro", $("#f-bairro").value.trim());
  set("pontuacao_min", $("#f-pmin").value);
  if ($("#f-semsite").checked) p.set("sem_site", "true");
  if ($("#f-tel").checked) p.set("com_telefone", "true");
  if (estado.campanhaId) p.set("campanha_id", estado.campanhaId);
  p.set("ordem", $("#f-ordem").value);
  return p;
}

function presenca(L) {
  const site = L.tipo_site === "proprio" && L.site;
  return `<span class="tags">
    <span class="tag ${site ? "" : "off"}" title="${site ? esc(L.site) : "Sem site próprio"}">Site</span>
    <span class="tag ${L.instagram ? "" : "off"}" title="${esc(L.instagram || "Sem Instagram")}">IG</span>
    <span class="tag ${L.whatsapp ? "" : "off"}" title="${L.whatsapp ? "Celular (WhatsApp provável)" : "Sem celular"}">WA</span>
  </span>`;
}

function barraPts(v) {
  const limiar = estado.meta?.limiar_qualificado ?? 55;
  return `<div class="pts"><b>${esc(v)}</b><div class="barra"><i class="${v >= limiar ? "alto" : ""}" style="width:${Math.max(0, Math.min(100, v))}%"></i></div></div>`;
}

async function carregarLeads() {
  const p = filtros();
  const exp = new URLSearchParams(p);
  $("#btnExport").href = "/api/exportar?" + exp.toString();
  p.set("limit", estado.limit);
  p.set("offset", estado.offset);
  const data = await api("/api/leads?" + p.toString());
  const tb = $("#tbodyLeads");
  estado.idsVisiveis = data.itens.map((L) => L.id);

  if (!data.itens.length) {
    const msg = data.total_db
      ? "Nenhum lead com esses filtros."
      : 'Nenhum lead ainda. Rode uma campanha na aba <a href="#campanhas" data-ir="campanhas">Campanhas</a>.';
    tb.innerHTML = `<tr><td colspan="6" class="vazio">${msg}</td></tr>`;
    const ir = $("[data-ir]", tb);
    if (ir) ir.onclick = (e) => (e.preventDefault(), abrirAba("campanhas"));
  } else {
    tb.innerHTML = data.itens
      .map(
        (L) => `<tr data-id="${L.id}" class="${estado.leadAtual?.id === L.id ? "sel" : ""}">
          <td>${barraPts(L.pontuacao)}</td>
          <td class="nome"><strong>${esc(L.nome)}</strong><span>${esc(L.categoria || "")}${L.avaliacoes ? ` · ${esc(L.nota ?? "–")}★ ${esc(L.avaliacoes)}` : ""}</span></td>
          <td>${esc(L.bairro || "—")}</td>
          <td>${presenca(L)}</td>
          <td><span class="status ${esc(L.status)}">${esc(nomeStatus(L.status))}</span></td>
          <td class="trunc col-opc">${esc(L.proximo_passo || "")}</td>
        </tr>`
      )
      .join("");
    $$("tr[data-id]", tb).forEach((tr) => tr.addEventListener("click", () => abrirFicha(Number(tr.dataset.id))));
  }

  const ini = data.total_filtrado ? estado.offset + 1 : 0;
  const fim = estado.offset + data.itens.length;
  let info = `${ini}–${fim} de ${data.total_filtrado}`;
  if (data.total_filtrado !== data.total_db) info += ` (base: ${data.total_db})`;
  if (estado.campanhaId) info += ` · campanha #${estado.campanhaId} <button class="btn link mini" id="limparCamp">remover filtro</button>`;
  $("#pagInfo").innerHTML = info;
  const lc = $("#limparCamp");
  if (lc) lc.onclick = () => ((estado.campanhaId = null), recarregarLeads());
  $("#pagAnt").disabled = estado.offset === 0;
  $("#pagProx").disabled = fim >= data.total_filtrado;
}

const EM_ANDAMENTO = "contatado,respondeu,reuniao,proposta";

async function carregarResumo() {
  const r = await api("/api/resumo");
  const kpis = [
    ["Leads na base", r.total, {}],
    [`Qualificados (≥ ${r.limiar} pts)`, r.qualificados, { status: "qualificado" }],
    ["Em andamento", r.em_andamento, { status: EM_ANDAMENTO }],
    ["Fechados", r.fechados, { status: "fechado" }],
  ];
  $("#kpis").innerHTML = kpis
    .map(([l, v], i) => `<button type="button" class="kpi" data-i="${i}"><div class="v">${esc(v)}</div><div class="l">${esc(l)}</div></button>`)
    .join("");
  $$("#kpis .kpi").forEach((b) =>
    b.addEventListener("click", () => {
      const f = kpis[b.dataset.i][2];
      limparFiltros(false);
      $("#f-status").value = f.status || "";
      recarregarLeads();
    })
  );
}

function recarregarLeads() {
  estado.offset = 0;
  return carregarLeads().catch(falhar);
}

function limparFiltros(recarregar = true) {
  ["#f-q", "#f-segmento", "#f-status", "#f-bairro", "#f-pmin"].forEach((s) => ($(s).value = ""));
  $("#f-semsite").checked = false;
  $("#f-tel").checked = false;
  estado.campanhaId = null;
  if (recarregar) recarregarLeads();
}

$("#f-q").addEventListener("input", debounce(recarregarLeads));
$("#f-bairro").addEventListener("input", debounce(recarregarLeads));
$("#f-pmin").addEventListener("input", debounce(recarregarLeads));
["#f-segmento", "#f-status", "#f-ordem", "#f-semsite", "#f-tel"].forEach((s) =>
  $(s).addEventListener("change", recarregarLeads)
);
$("#btnLimpar").addEventListener("click", () => limparFiltros());
$("#pagAnt").addEventListener("click", () => {
  estado.offset = Math.max(0, estado.offset - estado.limit);
  carregarLeads().catch(falhar);
});
$("#pagProx").addEventListener("click", () => {
  estado.offset += estado.limit;
  carregarLeads().catch(falhar);
});

/* ── Ficha ──────────────────────────────────────────────────────────── */

function fecharFicha() {
  if (location.hash.startsWith("#lead-")) history.replaceState(null, "", "#leads");
  $("#ficha").classList.remove("aberta");
  $("#ficha").setAttribute("aria-hidden", "true");
  estado.leadAtual = null;
  $$("#tbodyLeads tr.sel").forEach((tr) => tr.classList.remove("sel"));
}

document.addEventListener("keydown", (e) => {
  if (e.target.matches("input, textarea, select")) return;
  if (e.key === "Escape") fecharFicha();
  if (!estado.leadAtual || !["j", "k"].includes(e.key)) return;
  const i = estado.idsVisiveis.indexOf(estado.leadAtual.id) + (e.key === "j" ? 1 : -1);
  if (i >= 0 && i < estado.idsVisiveis.length) abrirFicha(estado.idsVisiveis[i]);
});

function linhaComp(rotulo, c) {
  const pct = c.peso ? (100 * c.pontos) / c.peso : 0;
  return `<div class="comp"><span>${rotulo}</span><div class="barra"><i style="width:${pct}%"></i></div><span class="v">${c.pontos.toFixed(0)}/${c.peso.toFixed(0)}</span></div>`;
}

function sinaisTecnicos(e) {
  const sim = (v) => (v ? "sim" : "não");
  const linhas = [];
  if (e.tipo_site && e.tipo_site !== "proprio") {
    const tipos = { rede_social: "rede social", whatsapp: "link de WhatsApp", agregador: "página de links / plataforma", orgao_publico: "órgão público" };
    linhas.push(["Site informado", tipos[e.tipo_site] || e.tipo_site]);
  }
  if (e.analisou_site || e.http_status) {
    linhas.push(["HTTP", `${e.http_status ?? "—"}${e.tempo_ms ? ` · ${e.tempo_ms} ms` : ""}`]);
    linhas.push(["HTTPS", e.certificado_invalido ? "certificado inválido" : sim(!e.sem_https)]);
  }
  if (e.analisou_site) {
    linhas.push(["Mobile", sim(!e.sem_mobile)]);
    linhas.push(["Agendamento", sim(e.tem_agendamento_online)]);
    linhas.push(["WhatsApp no site", sim(e.tem_whatsapp)]);
    if (e.plataforma) linhas.push(["Plataforma", e.plataforma]);
    if (e.ano_rodape) linhas.push(["Ano no rodapé", e.ano_rodape]);
  }
  if (e.reserva_pelo_google) linhas.push(["Google", "aceita reserva pelo Maps"]);
  if (e.bloqueou_verificacao) linhas.push(["Verificação", "site bloqueou robôs; conferir no navegador"]);
  if (e.erro) linhas.push(["Erro", e.erro]);
  if (!linhas.length) return "";
  return `<details class="secao"><summary>Sinais técnicos</summary><dl class="dados">${linhas
    .map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`)
    .join("")}</dl></details>`;
}

async function abrirFicha(id) {
  let L;
  try {
    L = await api(`/api/leads/${id}`);
  } catch (e) {
    return falhar(e);
  }
  estado.leadAtual = L;
  history.replaceState(null, "", `#lead-${L.id}`);
  $$("#tbodyLeads tr").forEach((tr) => tr.classList.toggle("sel", Number(tr.dataset.id) === id));
  const e = typeof L.enriquecimento === "object" && L.enriquecimento ? L.enriquecimento : {};
  const c = L.componentes && L.componentes.porte ? L.componentes : null;
  const sepMotivos = (L.motivos || "").includes(";") ? ";" : ",";
  const motivos = (L.motivos || "").split(sepMotivos).map((m) => m.trim()).filter(Boolean);
  const meta = estado.meta;

  $("#fichaTopo").innerHTML = `
    <div>
      <h2>${esc(L.nome)}</h2>
      <div class="muted small">${esc(L.categoria || "")}${L.bairro ? " · " + esc(L.bairro) : ""} · ${esc(nomeSegmento(L.segmento_id))}</div>
    </div>
    <button class="btn mini x" type="button" id="btnFechar" title="Fechar (Esc)">Fechar</button>`;

  const contato = [
    ["Telefone", L.telefone ? `${esc(L.telefone)} <button class="btn link mini" data-copiar="${esc(L.telefone)}">copiar</button>` : ""],
    ["WhatsApp", L.whatsapp ? `<a href="${esc(L.whatsapp)}" target="_blank" rel="noopener">abrir conversa</a>` : ""],
    ["E-mail", L.email ? `<a href="mailto:${esc(L.email)}">${esc(L.email)}</a>` : ""],
    ["Site", L.site ? `<a href="${esc(urlExterna(L.site))}" target="_blank" rel="noopener">${esc(L.site.replace(/^https?:\/\/(www\.)?/, "").replace(/\/$/, ""))}</a>` : ""],
    ["Instagram", L.instagram ? `<a href="${esc(urlExterna(L.instagram))}" target="_blank" rel="noopener">${esc(L.instagram.replace(/^https?:\/\/(www\.)?instagram\.com\//, "@").replace(/\/$/, ""))}</a>` : ""],
    ["Facebook", L.facebook ? `<a href="${esc(urlExterna(L.facebook))}" target="_blank" rel="noopener">abrir</a>` : ""],
    ["Endereço", esc(L.endereco || "")],
    ["Google Maps", L.maps_url ? `<a href="${esc(L.maps_url)}" target="_blank" rel="noopener">ver no Maps</a>${L.avaliacoes ? ` · ${esc(L.nota ?? "–")}★ (${esc(L.avaliacoes)} avaliações)` : ""}` : L.avaliacoes ? `${esc(L.nota ?? "–")}★ (${esc(L.avaliacoes)})` : ""],
  ].filter(([, v]) => v);

  const hist = (L.historico || [])
    .map((h) => {
      const desc = h.campo === "rascunho" ? "rascunho gerado" : `${esc(h.campo)}: ${esc(h.valor_anterior || "—")} → ${esc(h.valor_novo || "—")}`;
      return `<li><time>${fmtData(h.em)}</time><span>${desc}${h.quem ? ` <span class="muted">· ${esc(h.quem)}</span>` : ""}</span></li>`;
    })
    .join("");

  $("#fichaCorpo").innerHTML = `
    ${L.nao_contatar ? `<div class="secao"><div class="alerta">Na lista não contatar. Abordagem e rascunho bloqueados.</div></div>` : ""}
    <div class="secao">
      <div class="placar">
        <div class="grande">${esc(L.pontuacao)}<small>de 100</small></div>
        <div>${
          c
            ? linhaComp("Porte", c.porte) + linhaComp("Necessidade", c.necessidade) + linhaComp("Aderência", c.aderencia) +
              (c.penalidade ? `<div class="comp pen"><span>Penalidade</span><span></span><span class="v">−${c.penalidade.toFixed(0)}</span></div>` : "")
            : `<span class="muted small">Detalhamento indisponível. Use “Recalcular pontuações” em Campanhas.</span>`
        }</div>
      </div>
      <div class="motivos" style="margin-top:12px">${motivos.map((m) => `<span>${esc(m)}</span>`).join("")}</div>
    </div>

    <div class="secao">
      <h3>Contato</h3>
      <dl class="dados">${contato.map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join("") || "<dd class='muted'>Sem dados de contato.</dd>"}</dl>
    </div>

    <div class="secao">
      <h3>Acompanhamento</h3>
      <div class="form-ficha">
        <label class="campo">Status
          <select id="lead-status">${meta.status.map((s) => `<option value="${s.id}" ${s.id === L.status ? "selected" : ""}>${esc(s.nome)}</option>`).join("")}</select>
        </label>
        <label class="campo cheio">Próximo passo<input id="lead-prox" value="${esc(L.proximo_passo || "")}" placeholder="ex.: ligar terça às 10h" /></label>
        <label class="campo cheio">Anotações<textarea id="lead-notas" rows="3">${esc(L.anotacoes || "")}</textarea></label>
        <div class="cheio"><button class="btn primario" type="button" id="btnSalvarLead">Salvar</button></div>
      </div>
    </div>

    <div class="secao">
      <h3>Rascunho de abordagem</h3>
      <textarea id="rascunhoBox" class="rascunho" rows="7" placeholder="Gere um rascunho a partir dos motivos detectados.">${esc(L.rascunho || "")}</textarea>
      <div class="linha" style="margin-top:8px">
        <button class="btn" type="button" id="btnRascunho" ${L.nao_contatar ? "disabled" : ""}>${L.rascunho ? "Gerar de novo" : "Gerar rascunho"}</button>
        <button class="btn" type="button" id="btnCopiar">Copiar</button>
        ${L.whatsapp && !L.nao_contatar ? `<button class="btn primario" type="button" id="btnWa">Enviar pelo WhatsApp</button>` : ""}
      </div>
      <p class="ajuda" style="margin-top:6px">${L.whatsapp
        ? "“Enviar pelo WhatsApp” abre a conversa com este texto e registra o contato. Revise antes de apertar enviar."
        : "Sem celular cadastrado: copie o texto e envie por outro canal."}</p>
    </div>

    ${sinaisTecnicos(e)}

    <div class="secao">
      <h3>Histórico</h3>
      ${hist ? `<ul class="hist">${hist}</ul>` : `<span class="muted small">Sem alterações.</span>`}
      <p class="ajuda" style="margin-top:8px">Origem: ${esc(L.origem)} · coletado em ${fmtData(L.coletado_em)}</p>
    </div>

    <div class="secao linha">
      <button class="btn perigo" type="button" id="btnNaoLead" ${L.nao_contatar ? "disabled" : ""}>Não contatar</button>
      <button class="btn perigo" type="button" id="btnExcluir">Excluir (LGPD)</button>
    </div>`;

  $("#ficha").classList.add("aberta");
  $("#ficha").setAttribute("aria-hidden", "false");
  $("#btnFechar").onclick = fecharFicha;
  $$("[data-copiar]", $("#fichaCorpo")).forEach((b) => (b.onclick = () => copiar(b.dataset.copiar)));
  $("#btnSalvarLead").onclick = salvarLead;
  $("#btnRascunho").onclick = gerarRascunho;
  $("#btnCopiar").onclick = () => copiar($("#rascunhoBox").value);
  const wa = $("#btnWa");
  if (wa)
    wa.onclick = async () => {
      if (!$("#rascunhoBox").value.trim()) await gerarRascunho();
      const r = await abrirWhatsApp(L.id, $("#rascunhoBox").value);
      if (r) {
        toast(`Envio registrado (hoje: ${r.enviados_hoje} de ${r.limite_diario}).`);
        await Promise.all([abrirFicha(L.id), carregarLeads(), carregarResumo()]);
      }
    };
  $("#btnNaoLead").onclick = async () => {
    if (!confirm(`Adicionar "${L.nome}" à lista não contatar?`)) return;
    try {
      await api("/api/nao-contatar", { method: "POST", body: JSON.stringify({ lead_id: L.id, motivo: "pedido via painel" }) });
      toast("Adicionado à lista não contatar.");
      await Promise.all([abrirFicha(L.id), carregarLeads(), carregarResumo()]);
    } catch (err) {
      falhar(err);
    }
  };
  $("#btnExcluir").onclick = async () => {
    if (!confirm("Excluir este lead e o histórico dele permanentemente?")) return;
    try {
      await api(`/api/leads/${L.id}`, { method: "DELETE" });
      fecharFicha();
      toast("Lead excluído.");
      await Promise.all([carregarLeads(), carregarResumo()]);
    } catch (err) {
      falhar(err);
    }
  };
}

async function copiar(texto) {
  if (!texto) return toast("Nada para copiar.", true);
  try {
    await navigator.clipboard.writeText(texto);
    toast("Copiado.");
  } catch (_) {
    toast("Não foi possível copiar automaticamente.", true);
  }
}

async function salvarLead() {
  const L = estado.leadAtual;
  if (!L) return;
  try {
    await api(`/api/leads/${L.id}`, {
      method: "PATCH",
      body: JSON.stringify({
        status: $("#lead-status").value,
        anotacoes: $("#lead-notas").value,
        proximo_passo: $("#lead-prox").value,
        quem: quem(),
      }),
    });
    toast("Salvo.");
    await Promise.all([abrirFicha(L.id), carregarLeads(), carregarResumo()]);
  } catch (e) {
    falhar(e);
  }
}

async function gerarRascunho() {
  const L = estado.leadAtual;
  if (!L) return;
  try {
    const r = await api(`/api/leads/${L.id}/rascunho`, { method: "POST", body: JSON.stringify({ quem: quem() }) });
    const box = $("#rascunhoBox");
    box.value = r.rascunho;
    box.dispatchEvent(new Event("input"));
    $("#btnRascunho").textContent = "Gerar de novo";
  } catch (e) {
    falhar(e);
  }
}

/* ── Campanhas ──────────────────────────────────────────────────────── */

const ROTULO_CAMPANHA = { ok: "Concluída", rodando: "Rodando", erro: "Erro", cancelada: "Cancelada", pendente: "Pendente" };

async function carregarCampanhas() {
  const lista = await api("/api/campanhas");
  const tb = $("#listaCampanhas");
  if (!lista.length) {
    tb.innerHTML = `<tr><td colspan="6" class="vazio">Nenhuma campanha ainda.</td></tr>`;
    return;
  }
  tb.innerHTML = lista
    .map((c) => {
      const termos = Array.isArray(c.termos) ? c.termos.join(", ") : c.termos;
      const alertas = [
        c.bloqueio_suspeito ? `<span class="status contatado">bloqueio suspeito</span>` : "",
        c.erro ? `<div class="small" style="color:var(--danger)">${esc(c.erro)}</div>` : "",
      ].join(" ");
      return `<tr>
        <td class="muted num">${esc(c.id)}</td>
        <td><strong>${esc(c.nome)}</strong><div class="small muted">${esc(termos)} · prof. ${esc(c.profundidade)}${c.grid > 1 ? ` · grid ${c.grid}×${c.grid}` : ""}</div>${alertas}</td>
        <td>${esc(ROTULO_CAMPANHA[c.status] || c.status)}</td>
        <td class="num">${esc(c.linhas_coletadas ?? 0)}</td>
        <td class="small muted">${fmtData(c.criada_em)}</td>
        <td><div class="linha" style="flex-wrap:nowrap">
          ${c.linhas_coletadas ? `<button class="btn mini" data-ver="${c.id}">Ver leads</button>` : ""}
          ${c.status === "ok" ? `<button class="btn mini" data-rep="${c.id}" title="Refaz análise dos sites e pontuação sem consultar o Google">Reprocessar</button>` : ""}
        </div></td>
      </tr>`;
    })
    .join("");
  $$("[data-ver]", tb).forEach((b) =>
    b.addEventListener("click", () => {
      limparFiltros(false);
      estado.campanhaId = Number(b.dataset.ver);
      abrirAba("leads");
    })
  );
  $$("[data-rep]", tb).forEach((b) =>
    b.addEventListener("click", async () => {
      try {
        await api(`/api/campanhas/${b.dataset.rep}/reprocessar`, { method: "POST" });
        iniciarPoll();
      } catch (e) {
        falhar(e);
      }
    })
  );
}

function termosDoSegmento() {
  const seg = estado.meta.segmentos.find((s) => s.id === $("#c-segmento").value);
  if (seg) $("#c-termos").value = seg.termos.join("\n");
}

$("#btnTermosSeg").addEventListener("click", termosDoSegmento);

$("#btnCampanha").addEventListener("click", async () => {
  const termos = $("#c-termos").value.split("\n").map((t) => t.trim()).filter(Boolean);
  const grid = Number($("#c-grid").value) || 1;
  if (grid > 1 && !confirm(`Isso cria ${grid * grid} jobs em sequência no Google Maps. Continuar?`)) return;
  try {
    await api("/api/campanhas", {
      method: "POST",
      body: JSON.stringify({
        nome: $("#c-nome").value.trim() || "Campanha",
        cidade: $("#c-cidade").value.trim(),
        segmento_id: $("#c-segmento").value || null,
        termos,
        profundidade: Number($("#c-depth").value) || 5,
        grid,
      }),
    });
    iniciarPoll();
  } catch (e) {
    falhar(e);
  }
});

$("#btnCancelar").addEventListener("click", async () => {
  try {
    const r = await api("/api/campanhas/cancelar", { method: "POST" });
    toast(r.msg);
  } catch (e) {
    falhar(e);
  }
});

$("#btnRepontuar").addEventListener("click", async () => {
  try {
    const r = await api("/api/leads/repontuar", { method: "POST" });
    toast(`Pontuações recalculadas: ${r.alterados} de ${r.total} mudaram.`);
  } catch (e) {
    falhar(e);
  }
});

function desenharProgresso(p) {
  const box = $("#campanhaLog");
  box.hidden = !(p.log && p.log.length);
  box.innerHTML = (p.log || [])
    .map((l) => {
      const hora = new Date(l.em).toLocaleTimeString("pt-BR");
      return `<div class="${l.msg.startsWith("ERRO") ? "erro" : ""}">${hora}  ${esc(l.msg)}</div>`;
    })
    .join("");
  box.scrollTop = box.scrollHeight;
  $("#btnCampanha").disabled = !!p.rodando;
  $("#btnCancelar").hidden = !p.rodando;
}

function iniciarPoll() {
  if (estado.pollTimer) return;
  const tick = async () => {
    let p;
    try {
      p = await api("/api/campanhas/progresso");
    } catch (_) {
      return;
    }
    desenharProgresso(p);
    if (!p.rodando) {
      clearInterval(estado.pollTimer);
      estado.pollTimer = null;
      carregarSaude();
      carregarCampanhas().catch(falhar);
      carregarResumo().catch(() => {});
      if (p.erro) toast("Campanha com erro: " + p.erro, true);
      else if (p.resultado) toast(`Concluída: ${p.resultado.total} leads (${p.resultado.novos} novos).`);
    }
  };
  estado.pollTimer = setInterval(tick, 2000);
  tick();
  carregarSaude();
  carregarCampanhas().catch(falhar);
}

/* ── WhatsApp (envio assistido) ─────────────────────────────────────── */

const fila = { itens: [], excluidos: [], resumo: {}, enviadosHoje: 0, limite: 30, extras: {} };

function filtrosEnvio() {
  const body = {
    segmento_id: $("#e-segmento").value || null,
    status: $("#e-status").value,
    pontuacao_min: $("#e-pmin").value === "" ? null : Number($("#e-pmin").value),
    bairro: $("#e-bairro").value.trim() || null,
    sem_site: $("#e-semsite").checked,
    incluir_contatados: $("#e-contatados").checked,
    quem: quem(),
  };
  return { ...body, ...fila.extras };
}

async function montarFila() {
  try {
    const r = await api("/api/envios/previa", { method: "POST", body: JSON.stringify(filtrosEnvio()) });
    fila.itens = r.itens.map((i) => ({ ...i, marcado: true, situacao: "pendente" }));
    fila.excluidos = r.excluidos;
    fila.resumo = r.resumo_exclusao;
    fila.enviadosHoje = r.enviados_hoje;
    fila.limite = r.limite_diario;
    renderFila();
  } catch (e) {
    falhar(e);
  }
}

const proximoDaFila = () => fila.itens.find((i) => i.marcado && i.situacao === "pendente");

function renderFila() {
  const feitas = fila.itens.filter((i) => i.situacao === "enviada").length;
  const marcadas = fila.itens.filter((i) => i.marcado).length;
  $("#filaInfo").textContent = fila.itens.length
    ? `${feitas} de ${marcadas} enviadas · hoje: ${fila.enviadosHoje} de ${fila.limite}`
    : `hoje: ${fila.enviadosHoje} de ${fila.limite}`;

  $("#filaLista").innerHTML =
    fila.itens
      .map(
        (i) => `<tr class="${i === proximoDaFila() ? "sel" : ""}">
          <td><input type="checkbox" data-fila="${i.id}" ${i.marcado ? "checked" : ""} ${i.situacao !== "pendente" ? "disabled" : ""} /></td>
          <td>${barraPts(i.pontuacao)}</td>
          <td class="nome"><strong>${esc(i.nome)}</strong><span>${esc(i.bairro || "")}</span></td>
          <td>${esc(i.telefone)}</td>
          <td><span class="status ${i.situacao === "enviada" ? "fechado" : ""}">${{ pendente: "Pendente", enviada: "Enviada", pulada: "Pulada" }[i.situacao]}</span></td>
        </tr>`
      )
      .join("") || `<tr><td colspan="5" class="vazio">Nenhuma empresa na fila.</td></tr>`;
  $$("[data-fila]").forEach((c) =>
    c.addEventListener("change", () => {
      fila.itens.find((i) => i.id === Number(c.dataset.fila)).marcado = c.checked;
      renderFila();
    })
  );

  const fora = Object.entries(fila.resumo).map(([m, n]) => `${n} ${m}`);
  $("#filaExcluidos").textContent = fora.length ? `Fora da fila: ${fora.join(" · ")}.` : "";

  const atual = proximoDaFila();
  const box = $("#filaAtual");
  if (!fila.itens.length) {
    box.innerHTML = `<p class="muted">Nenhuma empresa atende aos filtros.</p>`;
    return;
  }
  if (!atual) {
    box.innerHTML = `<p><strong>Fila concluída.</strong> ${feitas} mensagem(ns) enviada(s). Os leads enviados agora aparecem como <em>Contatado</em>.</p>`;
    return;
  }
  const noLimite = fila.enviadosHoje >= fila.limite;
  box.innerHTML = `
    <div><strong>${esc(atual.nome)}</strong> <span class="muted small">· ${esc(atual.telefone)} · ${esc(atual.pontuacao)} pts</span></div>
    <textarea id="filaMsg" class="rascunho" rows="9">${esc(atual.mensagem)}</textarea>
    <div class="linha">
      <button class="btn primario" type="button" id="btnEnviarAtual" ${noLimite ? "disabled" : ""}>Abrir no WhatsApp e marcar como enviada</button>
      <button class="btn" type="button" id="btnPular">Pular</button>
    </div>
    <p class="ajuda">${noLimite
      ? `Limite diário de ${fila.limite} envios atingido. Continue amanhã (ajuste em <code>config/pesos.json</code>).`
      : "Revise o texto: ele vale só para esta empresa. O WhatsApp abre com a mensagem pronta; confira e aperte enviar."}</p>`;
  $("#filaMsg").addEventListener("input", (e) => (atual.mensagem = e.target.value));
  $("#btnEnviarAtual").onclick = () =>
    abrirWhatsApp(atual.id, atual.mensagem).then((r) => {
      if (!r) return;
      atual.situacao = "enviada";
      fila.enviadosHoje = r.enviados_hoje;
      renderFila();
    });
  $("#btnPular").onclick = () => {
    atual.situacao = "pulada";
    renderFila();
  };
}

// A aba é aberta antes da chamada para o navegador não tratá-la como pop-up.
async function abrirWhatsApp(leadId, texto) {
  if (!texto || !texto.trim()) {
    toast("A mensagem está vazia.", true);
    return null;
  }
  const aba = window.open("", "_blank");
  try {
    const r = await api(`/api/leads/${leadId}/whatsapp`, {
      method: "POST",
      body: JSON.stringify({ texto, quem: quem() }),
    });
    if (aba) aba.location = r.url;
    else window.open(r.url, "_blank");
    return r;
  } catch (e) {
    if (aba) aba.close();
    falhar(e);
    return null;
  }
}

function filaComFiltrosDosLeads() {
  $("#e-segmento").value = $("#f-segmento").value;
  $("#e-pmin").value = $("#f-pmin").value;
  $("#e-bairro").value = $("#f-bairro").value;
  $("#e-semsite").checked = $("#f-semsite").checked;
  const st = $("#f-status").value;
  if ([...$("#e-status").options].some((o) => o.value === st)) $("#e-status").value = st;
  fila.extras = {};
  if ($("#f-q").value.trim()) fila.extras.q = $("#f-q").value.trim();
  if (estado.campanhaId) fila.extras.campanha_id = estado.campanhaId;
  const origem = $("#e-origem");
  origem.hidden = !Object.keys(fila.extras).length;
  origem.innerHTML = `Também filtrando pela aba Leads${fila.extras.q ? ` (busca "${esc(fila.extras.q)}")` : ""}${fila.extras.campanha_id ? ` (campanha #${fila.extras.campanha_id})` : ""}. <button class="btn link mini" type="button" id="limparExtras">remover</button>`;
  const le = $("#limparExtras");
  if (le) le.onclick = () => ((fila.extras = {}), (origem.hidden = true));
  abrirAba("envios");
  montarFila();
}

$("#btnFila").addEventListener("click", montarFila);
$("#btnLote").addEventListener("click", filaComFiltrosDosLeads);
$("#filaTodos").addEventListener("change", (e) => {
  fila.itens.filter((i) => i.situacao === "pendente").forEach((i) => (i.marcado = e.target.checked));
  renderFila();
});

/* ── Funil ──────────────────────────────────────────────────────────── */

async function carregarFunil() {
  const f = await api("/api/funil");
  const max = Math.max(1, ...f.por_status.map((s) => s.qtd));
  $("#funilStatus").innerHTML = f.por_status
    .map(
      (s) => `<div class="funil-linha"><span>${esc(s.nome)}</span>
        <div class="barra"><i style="width:${(100 * s.qtd) / max}%"></i></div><span class="v">${esc(s.qtd)}</span></div>`
    )
    .join("");

  $("#funilTaxas").innerHTML =
    f.taxa_por_segmento
      .map(
        (t) => `<tr>
          <td>${esc(t.segmento)}</td>
          <td class="num">${esc(t.total)}</td>
          <td class="num">${esc(t.qualificados)}</td>
          <td class="num">${esc(t.contatados)}</td>
          <td class="num">${esc(t.engajados)}</td>
          <td class="num">${esc(t.fechados)}</td>
          <td class="num">${esc(t.media_pontuacao)}</td>
          <td class="num"><strong>${esc(t.taxa_resposta_pct.toLocaleString("pt-BR"))}%</strong></td>
        </tr>`
      )
      .join("") || `<tr><td colspan="8" class="vazio">Sem dados.</td></tr>`;
}

/* ── Não contatar ───────────────────────────────────────────────────── */

async function carregarNao() {
  const lista = await api("/api/nao-contatar");
  const tb = $("#listaNao");
  tb.innerHTML =
    lista
      .map(
        (n) => `<tr>
          <td>${esc(n.nome || "—")}</td><td class="num">${esc(n.telefone || "—")}</td>
          <td>${esc(n.email || "—")}</td><td class="trunc">${esc(n.motivo || "")}</td>
          <td class="small muted">${fmtData(n.criado_em, false)}</td>
          <td><button class="btn mini" data-del="${n.id}" type="button">Remover</button></td>
        </tr>`
      )
      .join("") || `<tr><td colspan="6" class="vazio">Lista vazia.</td></tr>`;
  $$("[data-del]", tb).forEach((b) => {
    b.onclick = async () => {
      if (!confirm("Remover da lista não contatar? Os leads continuam descartados até você mudar o status.")) return;
      await api(`/api/nao-contatar/${b.dataset.del}`, { method: "DELETE" }).catch(falhar);
      carregarNao().catch(falhar);
    };
  });
}

$("#btnNao").addEventListener("click", async () => {
  const campos = ["#nc-tel", "#nc-email", "#nc-nome", "#nc-motivo"];
  const [telefone, email, nome, motivo] = campos.map((s) => $(s).value.trim() || null);
  try {
    const r = await api("/api/nao-contatar", { method: "POST", body: JSON.stringify({ telefone, email, nome, motivo }) });
    campos.forEach((s) => ($(s).value = ""));
    toast(r.leads_descartados ? `Adicionado. ${r.leads_descartados} lead(s) descartado(s).` : "Adicionado.");
    carregarNao().catch(falhar);
  } catch (e) {
    falhar(e);
  }
});

/* ── Início ─────────────────────────────────────────────────────────── */

(async () => {
  estado.meta = await api("/api/meta");
  const m = estado.meta;
  opcoes($("#f-segmento"), m.segmentos, (s) => s.id, (s) => s.nome);
  opcoes($("#e-segmento"), m.segmentos, (s) => s.id, (s) => s.nome);
  opcoes($("#c-segmento"), m.segmentos, (s) => s.id, (s) => `${s.nome} (${s.prioridade})`);
  opcoes($("#f-status"), [{ id: EM_ANDAMENTO, nome: "Em andamento (todos)" }, ...m.status], (s) => s.id, (s) => s.nome);
  opcoes($("#voce"), m.responsaveis);
  $("#c-segmento").value = "clinicas";
  $("#c-cidade").value = m.cidade_padrao;
  $("#c-depth").value = m.coleta.profundidade_padrao || 5;
  try {
    $("#voce").value = localStorage.getItem("radar.voce") || "";
  } catch (_) {}

  const link = location.hash.match(/^#lead-(\d+)$/);
  abrirAba(link ? "leads" : location.hash.slice(1) || "leads");
  if (link) abrirFicha(Number(link[1]));
  carregarSaude();
  setInterval(carregarSaude, 20000);
  const p = await api("/api/campanhas/progresso");
  if (p.rodando) iniciarPoll();
  else desenharProgresso(p);
})().catch(falhar);
