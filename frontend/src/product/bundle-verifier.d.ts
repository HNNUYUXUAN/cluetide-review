export interface VerifiedBundle {
  manifest: { files: Record<string, { sha256: string; size: number }> };
  report: { case_id: string; revision: number; parent_manifest_hash: string | null };
  archiveHash: string;
  manifestHash: string;
  verifiedFiles: string[];
}
export function verifyBundle(bytes: Uint8Array): Promise<VerifiedBundle>;
export function sha256(bytes: Uint8Array): Promise<string>;
