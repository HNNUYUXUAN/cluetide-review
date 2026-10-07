import { createRoot } from "react-dom/client";
import ProductApp from "./product/ProductApp";
import "./styles.css";
import "./product/product.css";

createRoot(document.getElementById("root")!).render(<ProductApp />);
