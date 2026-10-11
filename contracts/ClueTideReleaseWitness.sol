// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

/// @title Explicit release authorization and signed execution observations
/// @notice A receipt authenticates its signer; external publication requires independent verification.
contract ClueTideReleaseWitness {
    struct TaskRecord {
        address owner;
        address proposer;
        address reviewer;
        address executor;
        address receiptSigner;
        bytes32 targetHash;
        uint256 versionCount;
        uint256 attemptCount;
        uint64 createdAt;
    }

    // Decisions: 0 pending, 1 approved, 2 revoked, 3 correction requested, 4 consumed.
    struct VersionRecord {
        uint256 versionId;
        uint256 parentVersionId;
        bytes32 manifestHash;
        bytes32 artifactHash;
        uint64 deadline;
        uint8 decision;
        uint64 approvedAt;
        uint256 attemptId;
    }

    // Results: 0 no receipt, 1 success, 2 failure, 3 unknown observation.
    struct AttemptRecord {
        uint256 attemptId;
        uint256 versionId;
        uint64 startedAt;
        bytes32 receiptHash;
        uint8 result;
        uint64 recordedAt;
    }

    mapping(bytes32 => TaskRecord) private tasks;
    mapping(bytes32 => mapping(uint256 => VersionRecord)) private versions;
    mapping(bytes32 => mapping(uint256 => AttemptRecord)) private attempts;
    bytes32 private constant DOMAIN_TYPEHASH = keccak256("EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)");
    bytes32 private constant RECEIPT_TYPEHASH = keccak256("Receipt(bytes32 taskId,uint256 versionId,uint256 attemptId,bytes32 receiptHash,uint8 result)");
    uint256 private constant HALF_ORDER = 0x7fffffffffffffffffffffffffffffff5d576e7357a4501ddfe92f46681b20a0;

    error InvalidIdentifier();
    error InvalidHash();
    error InvalidRole();
    error TaskExists();
    error UnknownTask();
    error UnknownVersion();
    error UnknownAttempt();
    error Unauthorized();
    error StaleVersion();
    error CommitmentMismatch();
    error InvalidDeadline();
    error InvalidDecision();
    error InvalidSignature();
    error InvalidResult();
    error ReceiptConflict();

    event TaskCreated(bytes32 indexed taskId, address indexed owner, bytes32 targetHash);
    event VersionProposed(bytes32 indexed taskId, uint256 indexed versionId, bytes32 manifestHash, bytes32 artifactHash, uint64 deadline);
    event DecisionRecorded(bytes32 indexed taskId, uint256 indexed versionId, uint8 decision);
    event ApprovalConsumed(bytes32 indexed taskId, uint256 indexed versionId, uint256 indexed attemptId);
    event ReceiptRecorded(bytes32 indexed taskId, uint256 indexed attemptId, bytes32 receiptHash, uint8 result);

    function createTask(bytes32 taskId, address proposer, address reviewer, address executor, address receiptSigner, bytes32 targetHash) external {
        if (taskId == bytes32(0)) revert InvalidIdentifier();
        if (tasks[taskId].owner != address(0)) revert TaskExists();
        if (proposer == address(0) || reviewer == address(0) || executor == address(0) || receiptSigner == address(0) || proposer == reviewer) revert InvalidRole();
        if (targetHash == bytes32(0)) revert InvalidHash();
        tasks[taskId] = TaskRecord(msg.sender, proposer, reviewer, executor, receiptSigner, targetHash, 0, 0, uint64(block.timestamp));
        emit TaskCreated(taskId, msg.sender, targetHash);
    }

    function proposeVersion(bytes32 taskId, uint256 parentVersionId, bytes32 manifestHash, bytes32 artifactHash, uint64 deadline) external returns (uint256 versionId) {
        TaskRecord storage task = _task(taskId);
        if (msg.sender != task.proposer) revert Unauthorized();
        if (parentVersionId != task.versionCount) revert StaleVersion();
        if (manifestHash == bytes32(0) || artifactHash == bytes32(0)) revert InvalidHash();
        if (deadline <= block.timestamp) revert InvalidDeadline();
        versionId = ++task.versionCount;
        versions[taskId][versionId] = VersionRecord(versionId, parentVersionId, manifestHash, artifactHash, deadline, 0, 0, 0);
        emit VersionProposed(taskId, versionId, manifestHash, artifactHash, deadline);
    }

    function approve(bytes32 taskId, uint256 versionId, bytes32 manifestHash, bytes32 artifactHash, uint64 deadline) external {
        TaskRecord storage task = _task(taskId);
        if (msg.sender != task.reviewer) revert Unauthorized();
        VersionRecord storage version = _current(taskId, versionId, task);
        _commitments(version, manifestHash, artifactHash);
        if (version.deadline != deadline) revert CommitmentMismatch();
        if (version.deadline < block.timestamp) revert InvalidDeadline();
        if (version.decision != 0) revert InvalidDecision();
        version.decision = 1;
        version.approvedAt = uint64(block.timestamp);
        emit DecisionRecorded(taskId, versionId, 1);
    }

    function revoke(bytes32 taskId, uint256 versionId) external {
        TaskRecord storage task = _task(taskId);
        if (msg.sender != task.reviewer && msg.sender != task.owner) revert Unauthorized();
        _decide(taskId, versionId, task, 2);
    }

    function requestCorrection(bytes32 taskId, uint256 versionId) external {
        TaskRecord storage task = _task(taskId);
        if (msg.sender != task.reviewer) revert Unauthorized();
        _decide(taskId, versionId, task, 3);
    }

    /// @notice Consumption is the authorization-use boundary; later versions do not cancel this attempt.
    function consume(bytes32 taskId, uint256 versionId, bytes32 manifestHash, bytes32 artifactHash) external returns (uint256 attemptId) {
        TaskRecord storage task = _task(taskId);
        if (msg.sender != task.executor) revert Unauthorized();
        VersionRecord storage version = _current(taskId, versionId, task);
        _commitments(version, manifestHash, artifactHash);
        if (version.decision != 1) revert InvalidDecision();
        if (version.deadline < block.timestamp) revert InvalidDeadline();
        version.decision = 4;
        attemptId = ++task.attemptCount;
        version.attemptId = attemptId;
        attempts[taskId][attemptId] = AttemptRecord(attemptId, versionId, uint64(block.timestamp), bytes32(0), 0, 0);
        emit ApprovalConsumed(taskId, versionId, attemptId);
    }

    /// @notice Any submitter may relay the preset EOA signer's immutable observation.
    function recordReceipt(bytes32 taskId, uint256 attemptId, bytes32 receiptHash, uint8 result, bytes calldata signature) external {
        TaskRecord storage task = _task(taskId);
        AttemptRecord storage attempt = _attempt(taskId, attemptId);
        if (receiptHash == bytes32(0)) revert InvalidHash();
        if (result < 1 || result > 3) revert InvalidResult();
        if (_recover(receiptDigest(taskId, attemptId, receiptHash, result), signature) != task.receiptSigner) revert InvalidSignature();
        if (attempt.receiptHash != bytes32(0)) {
            if (attempt.receiptHash != receiptHash || attempt.result != result) revert ReceiptConflict();
            return;
        }
        attempt.receiptHash = receiptHash;
        attempt.result = result;
        attempt.recordedAt = uint64(block.timestamp);
        emit ReceiptRecorded(taskId, attemptId, receiptHash, result);
    }

    function receiptDigest(bytes32 taskId, uint256 attemptId, bytes32 receiptHash, uint8 result) public view returns (bytes32) {
        AttemptRecord storage attempt = _attempt(taskId, attemptId);
        bytes32 domain = keccak256(abi.encode(DOMAIN_TYPEHASH, keccak256("ClueTideReleaseWitness"), keccak256("1"), block.chainid, address(this)));
        bytes32 message = keccak256(abi.encode(RECEIPT_TYPEHASH, taskId, attempt.versionId, attemptId, receiptHash, result));
        return keccak256(abi.encodePacked(hex"1901", domain, message));
    }

    function getTask(bytes32 taskId) external view returns (TaskRecord memory) { return _task(taskId); }
    function getVersion(bytes32 taskId, uint256 versionId) external view returns (VersionRecord memory) {
        _task(taskId);
        return _version(taskId, versionId);
    }
    function getAttempt(bytes32 taskId, uint256 attemptId) external view returns (AttemptRecord memory) {
        _task(taskId);
        return _attempt(taskId, attemptId);
    }

    function _task(bytes32 taskId) private view returns (TaskRecord storage task) {
        task = tasks[taskId];
        if (task.owner == address(0)) revert UnknownTask();
    }
    function _version(bytes32 taskId, uint256 versionId) private view returns (VersionRecord storage version) {
        version = versions[taskId][versionId];
        if (version.versionId == 0) revert UnknownVersion();
    }
    function _attempt(bytes32 taskId, uint256 attemptId) private view returns (AttemptRecord storage attempt) {
        attempt = attempts[taskId][attemptId];
        if (attempt.attemptId == 0) revert UnknownAttempt();
    }
    function _current(bytes32 taskId, uint256 versionId, TaskRecord storage task) private view returns (VersionRecord storage version) {
        version = _version(taskId, versionId);
        if (versionId != task.versionCount) revert StaleVersion();
    }
    function _commitments(VersionRecord storage version, bytes32 manifestHash, bytes32 artifactHash) private view {
        if (version.manifestHash != manifestHash || version.artifactHash != artifactHash) revert CommitmentMismatch();
    }
    function _decide(bytes32 taskId, uint256 versionId, TaskRecord storage task, uint8 decision) private {
        VersionRecord storage version = _current(taskId, versionId, task);
        if (version.decision > 1) revert InvalidDecision();
        version.decision = decision;
        emit DecisionRecorded(taskId, versionId, decision);
    }
    function _recover(bytes32 digest, bytes calldata signature) private pure returns (address signer) {
        if (signature.length != 65) revert InvalidSignature();
        bytes32 r;
        bytes32 s;
        uint8 v;
        assembly {
            r := calldataload(signature.offset)
            s := calldataload(add(signature.offset, 32))
            v := byte(0, calldataload(add(signature.offset, 64)))
        }
        if (uint256(s) > HALF_ORDER || (v != 27 && v != 28)) revert InvalidSignature();
        signer = ecrecover(digest, v, r, s);
        if (signer == address(0)) revert InvalidSignature();
    }
}
