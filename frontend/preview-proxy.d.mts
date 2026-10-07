import type { Plugin, ProxyOptions } from "vite";
export function allowedPreviewRequest(host: string | undefined, origin: string | undefined, fetchSite: string | undefined): boolean;
export const apiProxy: Record<string, ProxyOptions>;
export function previewOriginGuard(): Plugin;
