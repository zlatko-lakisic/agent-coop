import { render } from "preact";
import { App } from "./App";
import "./styles.css";

const params = new URLSearchParams(location.search);
const token = params.get("t") || sessionStorage.getItem("agentcoop_token") || "";
if (params.get("t")) {
  sessionStorage.setItem("agentcoop_token", token);
  params.delete("t");
  const next = location.pathname + (params.toString() ? "?" + params.toString() : "") + location.hash;
  history.replaceState({}, "", next);
}

render(<App token={token} />, document.getElementById("app")!);
