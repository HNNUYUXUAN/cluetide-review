// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

/// @title ClueTide evidence-version and review commitments
/// @notice A commitment records bytes and authorship; it does not certify facts.
/// @dev Readers use getters by known IDs. Events are optional notifications.
contract ClueTideRegistry {
    struct CaseRecord {
        address author;
        uint256 headVersionId;
        uint256 versionCount;
        uint256 reviewCount;
        uint64 createdAt;
    }

    struct VersionRecord {
        bytes32 caseId;
        uint256 versionId;
        uint256 parentVersionId;
        address author;
        bytes32 contentHash;
        string schemaVersion;
        string evidenceURI;
        uint64 createdAt;
    }

    struct ReviewRecord {
        uint256 reviewId;
        bytes32 caseId;
        uint256 versionId;
        bytes32 contentHash;
        address reviewer;
        uint8 decision;
        bytes32 reviewHash;
        string reviewURI;
        uint64 createdAt;
    }

    // Decisions: 1 = supported, 2 = correction requested, 3 = unresolved.
    uint256 public versionCount;
    uint256 public reviewCount;
    mapping(bytes32 => CaseRecord) private cases;
    mapping(uint256 => VersionRecord) private versions;
    mapping(uint256 => ReviewRecord) private reviews;
    mapping(bytes32 => uint256[]) private caseVersions;
    mapping(bytes32 => uint256[]) private caseReviews;

    error InvalidIdentifier();
    error InvalidContentHash();
    error InvalidMetadata();
    error CaseExists();
    error UnknownCase();
    error UnknownVersion();
    error UnknownReview();
    error UnauthorizedAuthor();
    error ParentCaseMismatch();
    error StaleParent();
    error ReviewCaseMismatch();
    error ReviewHashMismatch();
    error InvalidDecision();
    error IndexOutOfBounds();

    event CaseCreated(bytes32 indexed caseId, address indexed author, uint256 versionId);
    event VersionAdded(bytes32 indexed caseId, uint256 indexed versionId, uint256 parentVersionId, bytes32 contentHash);
    event ReviewAdded(bytes32 indexed caseId, uint256 indexed versionId, uint256 indexed reviewId, address reviewer);

    function createCase(bytes32 caseId, bytes32 contentHash, string calldata schemaVersion, string calldata evidenceURI)
        external returns (uint256 versionId)
    {
        if (caseId == bytes32(0)) revert InvalidIdentifier();
        if (cases[caseId].author != address(0)) revert CaseExists();
        _checkMetadata(contentHash, schemaVersion, evidenceURI);
        cases[caseId] = CaseRecord(msg.sender, 0, 0, 0, uint64(block.timestamp));
        versionId = _addVersion(caseId, 0, contentHash, schemaVersion, evidenceURI);
        emit CaseCreated(caseId, msg.sender, versionId);
    }

    function appendVersion(bytes32 caseId, uint256 parentVersionId, bytes32 contentHash, string calldata schemaVersion, string calldata evidenceURI)
        external returns (uint256 versionId)
    {
        CaseRecord storage record = cases[caseId];
        if (record.author == address(0)) revert UnknownCase();
        if (record.author != msg.sender) revert UnauthorizedAuthor();
        if (versions[parentVersionId].versionId == 0) revert UnknownVersion();
        if (versions[parentVersionId].caseId != caseId) revert ParentCaseMismatch();
        if (record.headVersionId != parentVersionId) revert StaleParent();
        _checkMetadata(contentHash, schemaVersion, evidenceURI);
        versionId = _addVersion(caseId, parentVersionId, contentHash, schemaVersion, evidenceURI);
    }

    function addReview(bytes32 caseId, uint256 versionId, bytes32 contentHash, uint8 decision, bytes32 reviewHash, string calldata reviewURI)
        external returns (uint256 reviewId)
    {
        if (cases[caseId].author == address(0)) revert UnknownCase();
        VersionRecord storage version = versions[versionId];
        if (version.versionId == 0) revert UnknownVersion();
        if (version.caseId != caseId) revert ReviewCaseMismatch();
        if (version.contentHash != contentHash) revert ReviewHashMismatch();
        if (reviewHash == bytes32(0)) revert InvalidContentHash();
        if (decision < 1 || decision > 3) revert InvalidDecision();
        if (bytes(reviewURI).length > 512) revert InvalidMetadata();
        reviewId = ++reviewCount;
        reviews[reviewId] = ReviewRecord(reviewId, caseId, versionId, contentHash, msg.sender, decision, reviewHash, reviewURI, uint64(block.timestamp));
        caseReviews[caseId].push(reviewId);
        cases[caseId].reviewCount++;
        emit ReviewAdded(caseId, versionId, reviewId, msg.sender);
    }

    function getCase(bytes32 caseId) external view returns (CaseRecord memory) {
        if (cases[caseId].author == address(0)) revert UnknownCase();
        return cases[caseId];
    }

    function getVersion(uint256 versionId) external view returns (VersionRecord memory) {
        if (versions[versionId].versionId == 0) revert UnknownVersion();
        return versions[versionId];
    }

    function getReview(uint256 reviewId) external view returns (ReviewRecord memory) {
        if (reviews[reviewId].reviewId == 0) revert UnknownReview();
        return reviews[reviewId];
    }

    function getVersionId(bytes32 caseId, uint256 index) external view returns (uint256) {
        if (cases[caseId].author == address(0)) revert UnknownCase();
        if (index >= caseVersions[caseId].length) revert IndexOutOfBounds();
        return caseVersions[caseId][index];
    }

    function getReviewId(bytes32 caseId, uint256 index) external view returns (uint256) {
        if (cases[caseId].author == address(0)) revert UnknownCase();
        if (index >= caseReviews[caseId].length) revert IndexOutOfBounds();
        return caseReviews[caseId][index];
    }

    function _checkMetadata(bytes32 contentHash, string calldata schemaVersion, string calldata evidenceURI) private pure {
        if (contentHash == bytes32(0)) revert InvalidContentHash();
        uint256 length = bytes(schemaVersion).length;
        if (length == 0 || length > 64 || bytes(evidenceURI).length > 512) revert InvalidMetadata();
    }

    function _addVersion(bytes32 caseId, uint256 parentVersionId, bytes32 contentHash, string calldata schemaVersion, string calldata evidenceURI)
        private returns (uint256 versionId)
    {
        versionId = ++versionCount;
        versions[versionId] = VersionRecord(caseId, versionId, parentVersionId, msg.sender, contentHash, schemaVersion, evidenceURI, uint64(block.timestamp));
        cases[caseId].headVersionId = versionId;
        cases[caseId].versionCount++;
        caseVersions[caseId].push(versionId);
        emit VersionAdded(caseId, versionId, parentVersionId, contentHash);
    }
}
