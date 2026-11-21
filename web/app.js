const $ = (id) => document.getElementById(id);
const escape = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
let session = JSON.parse(sessionStorage.getItem("pilot") || "null"),
  page = "Releases",
  selected = null,
  generation = 0,
  auditAction = "";
const notice = (msg) => {
  $("notice").textContent = msg;
};
async function api(path, body) {
  const response = await fetch("/api/v1/" + path, {
    method: body ? "POST" : "GET",
    headers: {
      "Content-Type": "application/json",
      ...(session ? { Authorization: "Bearer " + session.token } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await response.json();
  if (!response.ok) {
    if (response.status === 401 && session) {
      setSession(null);
    }
    throw Error(
      typeof data.detail === "string"
        ? data.detail
        : JSON.stringify(data.detail),
    );
  }
  return data;
}
function setSession(value) {
  session = value;
  sessionStorage.setItem("pilot", JSON.stringify(value));
  $("login").hidden = !!value;
  $("console").hidden = !value;
  $("identity").textContent = value ? value.email + " · " + value.role : "";
  generation++;
}
const time = (value) => new Date(value * 1000).toLocaleString();
const tag = (value) =>
  '<span class="tag ' + escape(value) + '">' + escape(value) + "</span>";
function table(headers, rows) {
  return (
    '<div class="table"><table><thead><tr>' +
    headers.map((h) => "<th>" + h + "</th>").join("") +
    "</tr></thead><tbody>" +
    rows
      .map(
        (r) => "<tr>" + r.map((c) => "<td>" + c + "</td>").join("") + "</tr>",
      )
      .join("") +
    "</tbody></table></div>"
  );
}
function input(label, name, type = "text") {
  return (
    "<label>" +
    label +
    '<input name="' +
    name +
    '" type="' +
    type +
    '" required></label>'
  );
}
function timeline(events) {
  return (
    events
      .map(
        (e) =>
          "<article><small>" +
          escape(time(e.time)) +
          " · " +
          (e.actor ? "User " + e.actor : "Worker") +
          "</small><p><strong>" +
          escape(e.action) +
          "</strong> " +
          tag(e.details.status || e.details.decision || "") +
          "</p><pre>" +
          escape(JSON.stringify(e.details, null, 2)) +
          "</pre></article>",
      )
      .join("") || "<p>No events recorded yet.</p>"
  );
}
async function render() {
  if (!session) return;
  const id = ++generation,
    role = session.role,
    canRelease = ["admin", "developer"].includes(role);
  $("nav").innerHTML = [
    "Releases",
    "Approvals",
    "Services",
    "Audit",
    "Sign out",
  ]
    .map(
      (n) =>
        '<button data-nav="' +
        n +
        '" aria-current="' +
        (n === page ? "page" : "false") +
        '">' +
        n +
        "</button>",
    )
    .join("");
  let html = "";
  if (page === "Releases" && selected) {
    const [r, events] = await Promise.all([
      api("releases/" + selected),
      api("releases/" + selected + "/events"),
    ]);
    html =
      '<button class="secondary" data-back>← All releases</button><h1>Release #' +
      r.id +
      "</h1>" +
      tag(r.status) +
      "<h2>Release timeline</h2>";
    const ops =
      r.status === "PENDING_APPROVAL"
        ? ["approve", "reject"]
        : r.status === "REVIEW"
          ? ["retry", r.previous_image ? "rollback" : "reject"]
          : r.status === "HEALTHY" && r.previous_image
            ? ["rollback"]
            : [];
    if (ops.length && ["admin", "approver"].includes(role))
      html +=
        '<form id="decision">' +
        input("Decision reason", "reason") +
        '<div class="actions">' +
        ops
          .map((a) => '<button value="' + a + '">' + a + "</button>")
          .join("") +
        "</div></form>";
    html += timeline(events);
  } else if (page === "Releases") {
    const [list, services] = await Promise.all([
      api("releases"),
      api("services"),
    ]);
    const byId = new Map(services.map((s) => [s.id, s]));
    html =
      '<h1>Releases</h1><div class="grid">' +
      [
        ["Registered targets", services.length],
        [
          "Awaiting approval",
          list.filter((r) => r.status === "PENDING_APPROVAL").length,
        ],
        ["Health review", list.filter((r) => r.status === "REVIEW").length],
      ]
        .map(
          ([k, v]) =>
            "<article>" + k + '<div class="metric">' + v + "</div></article>",
        )
        .join("") +
      "</div><h2>Recent deployments</h2>";
    html += list.length
      ? table(
          ["Release", "Service", "Environment", "Status", "Requested"],
          list.map((r) => [
            '<button data-release="' + r.id + '">#' + r.id + "</button>",
            escape(byId.get(r.service_id)?.name),
            escape(byId.get(r.service_id)?.environment),
            tag(r.status),
            time(r.created),
          ]),
        )
      : "<p>Register a service and import a successful build to request your first release.</p>";
  } else if (page === "Services") {
    const services = await api("services");
    html = "<h1>Services</h1>";
    if (role === "admin")
      html +=
        '<details><summary>Register service</summary><form id="register">' +
        input("Service name", "name") +
        input("GitHub repository (owner/repo)", "repository") +
        input("Deployment name", "deployment") +
        '<label>Environment<input name="environment" value="development" required></label><label>Container<input name="container" value="api" required></label><label>Namespace<input name="namespace" value="releasepilot-demo" required></label><button>Register target</button></form></details>';
    const groups = await Promise.all(
      services.map((s) => api("services/" + s.id + "/artifacts")),
    );
    services.forEach((s, i) => {
      html +=
        "<article><small>" +
        escape(s.environment) +
        "</small><h2>" +
        escape(s.name) +
        "</h2><p>" +
        escape(s.namespace + "/" + s.deployment) +
        " · " +
        escape(s.repository) +
        "</p><small>Healthy image</small><pre>" +
        escape(s.healthy_image || "Awaiting first healthy release") +
        "</pre>";
      if (canRelease)
        html +=
          '<form data-import="' +
          s.id +
          '">' +
          input("Successful GitHub Actions run ID", "run_id") +
          "<button>Verify and import build</button></form>";
      html +=
        groups[i]
          .map(
            (a) =>
              "<p><code>" +
              escape(a.sha.slice(0, 12)) +
              "</code> · Run " +
              escape(a.run_id) +
              " " +
              (canRelease
                ? '<button data-request="' +
                  s.id +
                  "," +
                  a.id +
                  '">Request release</button>'
                : "") +
              "</p>",
          )
          .join("") + "</article>";
    });
  } else if (page === "Approvals") {
    const pending = await api("releases?status=PENDING_APPROVAL");
    html =
      "<h1>Awaiting approval</h1>" +
      (pending.length
        ? table(
            ["Release", "Target ID", "Requested"],
            pending.map((r) => [
              '<button data-release="' + r.id + '">#' + r.id + "</button>",
              r.service_id,
              time(r.created),
            ]),
          )
        : "<p>No releases are waiting for approval.</p>");
  } else {
    html =
      '<h1>Audit history</h1><form id="filter"><label>Filter by event name<input name="action" placeholder="release.transition" value="' +
      escape(auditAction) +
      '"></label><button>Apply filter</button></form>' +
      timeline(
        await api("audit-events?action=" + encodeURIComponent(auditAction)),
      );
  }
  if (id === generation) $("content").innerHTML = html;
}
async function run(fn) {
  try {
    notice("");
    await fn();
  } catch (e) {
    notice(e.message);
  }
}
$("loginForm").onsubmit = (e) => {
  e.preventDefault();
  const body = Object.fromEntries(new FormData(e.target));
  run(async () => {
    setSession(await api("auth/login", body));
    e.target.reset();
    await render();
  });
};
$("console").onclick = (e) => {
  const b = e.target.closest("button");
  if (!b) return;
  run(async () => {
    if (b.dataset.nav === "Sign out") {
      await api("auth/logout", {});
      setSession(null);
      selected = null;
      return;
    }
    if (b.dataset.nav) {
      page = b.dataset.nav;
      selected = null;
    } else if (b.dataset.release) {
      page = "Releases";
      selected = b.dataset.release;
    } else if (b.hasAttribute("data-back")) {
      selected = null;
    } else if (b.dataset.request) {
      const [service_id, artifact_id] = b.dataset.request
        .split(",")
        .map(Number);
      b.disabled = true;
      try {
        const r = await api("releases", {
          service_id,
          artifact_id,
          request_key: crypto.randomUUID(),
        });
        selected = r.id;
        page = "Releases";
      } finally {
        b.disabled = false;
      }
    } else return;
    await render();
  });
};
$("console").onsubmit = (e) => {
  e.preventDefault();
  const f = e.target,
    body = Object.fromEntries(new FormData(f)),
    action = e.submitter?.value;
  run(async () => {
    if (f.id === "register") await api("services", body);
    else if (f.dataset.import)
      await api("services/" + f.dataset.import + "/artifacts", body);
    else if (f.id === "decision")
      await api("releases/" + selected + "/" + action, body);
    else if (f.id === "filter") auditAction = body.action.trim();
    await render();
  });
};
setSession(session);
run(render);
setInterval(() => {
  if (
    session &&
    ["Releases", "Approvals"].includes(page) &&
    !document.querySelector("#decision") &&
    document.visibilityState === "visible"
  )
    run(render);
}, 5000);
