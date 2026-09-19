import { createRoot } from "react-dom/client";
import App from "./app/App";
import "./styles.css";

const rootElement = document.getElementById("root");
if (!rootElement) throw new Error("ScholarTrace root rootElement is missing");
createRoot(rootElement).render(<App />);
