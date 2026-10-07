export function hasArchiveContext(value: unknown, expected: { caseId: string; localVersionId: number; contentHash: string; localReviewId?: number; reviewHash?: string; decision?: string }): boolean {
  if (!value || typeof value !== "object") return false;
  const record = value as Record<string, unknown>;
  if (record.id !== expected.caseId || !Array.isArray(record.versions)) return false;
  if (!record.versions.some(version => version && version.version_id === expected.localVersionId && version.content_hash === expected.contentHash)) return false;
  return expected.localReviewId === undefined || (Array.isArray(record.reviews) && record.reviews.some(review => review && review.review_id === expected.localReviewId && review.version_id === expected.localVersionId && review.review_hash === expected.reviewHash && review.decision === expected.decision));
}
