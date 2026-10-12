import { createRoot } from "react-dom/client";
import ReleaseApp from "./release/PublicReleaseApp";
import "./release/public-release.css";

createRoot(document.getElementById("root")!).render(<ReleaseApp />);
