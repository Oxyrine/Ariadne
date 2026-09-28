// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

/// @title AriadneLedger
/// @notice Lineage ledger for TReDS factoring units. Enforces current ownership,
///         one live pool per economic receivable, and computes each pool's
///         membership commitment itself. It does not judge eligibility: it only
///         requires an engine attestation and records it for anyone to re-check.
contract AriadneLedger {
    enum State { NONE, REGISTERED, FINANCED, POOLED, SETTLED, DEFAULTED }
    enum Op { ADD, REMOVE, SUBSTITUTE_OUT, SUBSTITUTE_IN, SETTLED, DEFAULTED }

    struct Unit {
        bytes32 receivableKey;  // canonical cross-platform fingerprint
        address registrar;      // the platform that registered it
        address owner;          // current holder; zero until financed
        State   state;
        bytes32 currentPoolId;  // non-zero only while POOLED
        uint64  invoiceDate;
        uint64  dueDate;
        uint128 amountPaise;    // face value in paise
        bytes32 buyerKey;       // keyed hash of buyer GSTIN
        bytes32 sellerKey;      // keyed hash of seller GSTIN
        uint32  transferCount;  // number of re-discounts
    }

    struct Pool {
        address originator;
        bytes32 rulesHash;       // hash of the human-signed rule set
        bytes32 groupMapHash;    // hash of the frozen buyer-group map
        bytes32 evidenceHash;    // hash of the scored evidence set
        bytes32 commitment;      // rolling hash of every membership event
        uint32  memberCount;     // live members
        uint32  manifestVersion; // 0 before sealing; 1 at seal; +1 per substitution
        bool    isSealed;
    }

    address public admin;
    address public engineSigner;
    mapping(address => bool) public isRegistrar;

    mapping(bytes32 => Unit)    public units;        // by unitId
    mapping(bytes32 => bytes32) public activeUnitOf; // receivableKey => unitId (never released)
    mapping(bytes32 => bytes32) public livePoolOf;   // receivableKey => poolId, zero if none
    mapping(bytes32 => Pool)    public pools;

    bytes32 public immutable DOMAIN_SEPARATOR;
    bytes32 public constant ATTESTATION_TYPEHASH = keccak256(
        "Attestation(bytes32 poolId,bytes32 unitId,bytes32 receivableKey,bytes32 rulesHash,uint32 manifestVersion)"
    );
    uint256 private constant HALF_N = 0x7fffffffffffffffffffffffffffffff5d576e7357a4501ddfe92f46681b20a0;

    error NotAdmin();
    error NotRegistrar();
    error NotUnitRegistrar(bytes32 unitId);
    error NotOwner(bytes32 unitId, address caller);
    error NotOriginator(bytes32 poolId, address caller);
    error WrongState(bytes32 unitId, State expected, State actual);
    error DuplicateReceivable(bytes32 receivableKey);
    error AlreadyEncumbered(bytes32 receivableKey, bytes32 poolId);
    error PoolAlreadySealed(bytes32 poolId);
    error PoolNotSealed(bytes32 poolId);
    error PoolExists(bytes32 poolId);
    error UnknownPool(bytes32 poolId);
    error NotInPool(bytes32 poolId, bytes32 unitId);
    error EmptyPool(bytes32 poolId);
    error NotYetDue(bytes32 unitId);
    error ZeroAddress();
    error BadRecipient(address to);
    error BadAttestation(bytes32 unitId);

    event RegistrarSet(address indexed registrar, bool allowed);
    event EngineSignerSet(address indexed signer);
    event UnitRegistered(
        bytes32 indexed unitId, bytes32 indexed receivableKey, address indexed registrar,
        uint64 invoiceDate, uint64 dueDate, uint128 amountPaise, bytes32 buyerKey, bytes32 sellerKey
    );
    event UnitFinanced(bytes32 indexed unitId, address indexed financier);
    event UnitTransferred(bytes32 indexed unitId, address indexed from, address indexed to, uint32 transferCount);
    event PoolCreated(
        bytes32 indexed poolId, address indexed originator, bytes32 rulesHash, bytes32 groupMapHash, bytes32 evidenceHash
    );
    event MembershipChanged(
        bytes32 indexed poolId, Op op, bytes32 indexed unitId, bytes32 commitment, uint32 manifestVersion
    );
    event PoolSealed(bytes32 indexed poolId, bytes32 commitment);
    event UnitSettled(bytes32 indexed unitId);
    event UnitDefaulted(bytes32 indexed unitId);

    constructor(address engineSigner_) {
        if (engineSigner_ == address(0)) revert ZeroAddress();
        admin = msg.sender;
        engineSigner = engineSigner_;
        DOMAIN_SEPARATOR = keccak256(abi.encode(
            keccak256("EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)"),
            keccak256("Ariadne"), keccak256("1"), block.chainid, address(this)
        ));
        emit EngineSignerSet(engineSigner_);
    }

    // ---------------------------------------------------------------- admin

    function setRegistrar(address registrar, bool allowed) external {
        if (msg.sender != admin) revert NotAdmin();
        if (registrar == address(0)) revert ZeroAddress();
        isRegistrar[registrar] = allowed;
        emit RegistrarSet(registrar, allowed);
    }

    function setEngineSigner(address signer) external {
        if (msg.sender != admin) revert NotAdmin();
        if (signer == address(0)) revert ZeroAddress();
        engineSigner = signer;
        emit EngineSignerSet(signer);
    }

    // ------------------------------------------------------------ registrar

    function registerUnit(
        bytes32 unitId, bytes32 receivableKey, uint64 invoiceDate, uint64 dueDate,
        uint128 amountPaise, bytes32 buyerKey, bytes32 sellerKey
    ) external {
        if (!isRegistrar[msg.sender]) revert NotRegistrar();
        Unit storage u = units[unitId];
        if (u.state != State.NONE) revert WrongState(unitId, State.NONE, u.state);
        if (activeUnitOf[receivableKey] != bytes32(0)) revert DuplicateReceivable(receivableKey);
        activeUnitOf[receivableKey] = unitId;
        u.receivableKey = receivableKey;
        u.registrar = msg.sender;
        u.state = State.REGISTERED;
        u.invoiceDate = invoiceDate;
        u.dueDate = dueDate;
        u.amountPaise = amountPaise;
        u.buyerKey = buyerKey;
        u.sellerKey = sellerKey;
        emit UnitRegistered(unitId, receivableKey, msg.sender, invoiceDate, dueDate, amountPaise, buyerKey, sellerKey);
    }

    function recordFinancing(bytes32 unitId, address financier) external {
        Unit storage u = _ownRegistrarUnit(unitId);
        if (u.state != State.REGISTERED) revert WrongState(unitId, State.REGISTERED, u.state);
        if (financier == address(0)) revert ZeroAddress();
        u.owner = financier;
        u.state = State.FINANCED;
        emit UnitFinanced(unitId, financier);
    }

    function settle(bytes32 unitId) external {
        Unit storage u = _ownRegistrarUnit(unitId);
        _requireLive(unitId, u);
        if (u.state == State.POOLED) _leavePool(unitId, u, Op.SETTLED);
        u.state = State.SETTLED;
        emit UnitSettled(unitId);
    }

    function markDefault(bytes32 unitId) external {
        Unit storage u = _ownRegistrarUnit(unitId);
        _requireLive(unitId, u);
        if (block.timestamp <= u.dueDate) revert NotYetDue(unitId);
        if (u.state == State.POOLED) _leavePool(unitId, u, Op.DEFAULTED);
        u.state = State.DEFAULTED;
        emit UnitDefaulted(unitId);
    }

    // ---------------------------------------------------------------- owner

    function rediscount(bytes32 unitId, address to) external {
        Unit storage u = units[unitId];
        if (u.owner != msg.sender) revert NotOwner(unitId, msg.sender);
        if (u.state == State.POOLED) revert AlreadyEncumbered(u.receivableKey, u.currentPoolId);
        if (u.state != State.FINANCED) revert WrongState(unitId, State.FINANCED, u.state);
        if (to == address(0) || to == msg.sender) revert BadRecipient(to);
        u.owner = to;
        u.transferCount += 1;
        emit UnitTransferred(unitId, msg.sender, to, u.transferCount);
    }

    // ----------------------------------------------------------- originator

    function createPool(bytes32 poolId, bytes32 rulesHash, bytes32 groupMapHash, bytes32 evidenceHash) external {
        if (poolId == bytes32(0) || pools[poolId].originator != address(0)) revert PoolExists(poolId);
        Pool storage p = pools[poolId];
        p.originator = msg.sender;
        p.rulesHash = rulesHash;
        p.groupMapHash = groupMapHash;
        p.evidenceHash = evidenceHash;
        emit PoolCreated(poolId, msg.sender, rulesHash, groupMapHash, evidenceHash);
    }

    function addToPool(bytes32 poolId, bytes32 unitId, bytes calldata attestation) external {
        Pool storage p = _originatorPool(poolId);
        if (p.isSealed) revert PoolAlreadySealed(poolId);
        Unit storage u = _poolableUnit(unitId);
        _checkAttestation(poolId, unitId, u.receivableKey, p.rulesHash, p.manifestVersion, attestation);
        _enter(poolId, p, unitId, u, Op.ADD);
    }

    function removeFromPool(bytes32 poolId, bytes32 unitId) external {
        Pool storage p = _originatorPool(poolId);
        if (p.isSealed) revert PoolAlreadySealed(poolId);
        _exit(poolId, p, unitId, Op.REMOVE);
    }

    function sealPool(bytes32 poolId) external {
        Pool storage p = _originatorPool(poolId);
        if (p.isSealed) revert PoolAlreadySealed(poolId);
        if (p.memberCount == 0) revert EmptyPool(poolId);
        p.isSealed = true;
        p.manifestVersion = 1;
        emit PoolSealed(poolId, p.commitment);
    }

    /// @dev The attestation must cover the pool after the swap, i.e. manifestVersion + 1.
    function substitute(bytes32 poolId, bytes32 outId, bytes32 inId, bytes calldata attestation) external {
        Pool storage p = _originatorPool(poolId);
        if (!p.isSealed) revert PoolNotSealed(poolId);
        Unit storage inU = _poolableUnit(inId);
        uint32 next = p.manifestVersion + 1;
        _checkAttestation(poolId, inId, inU.receivableKey, p.rulesHash, next, attestation);
        p.manifestVersion = next;
        _exit(poolId, p, outId, Op.SUBSTITUTE_OUT);
        _enter(poolId, p, inId, inU, Op.SUBSTITUTE_IN);
    }

    // ---------------------------------------------------------------- views

    function attestationDigest(
        bytes32 poolId, bytes32 unitId, bytes32 receivableKey, bytes32 rulesHash, uint32 manifestVersion
    ) public view returns (bytes32) {
        bytes32 structHash = keccak256(abi.encode(
            ATTESTATION_TYPEHASH, poolId, unitId, receivableKey, rulesHash, manifestVersion
        ));
        return keccak256(abi.encodePacked("\x19\x01", DOMAIN_SEPARATOR, structHash));
    }

    // ------------------------------------------------------------- internal

    function _ownRegistrarUnit(bytes32 unitId) private view returns (Unit storage u) {
        if (!isRegistrar[msg.sender]) revert NotRegistrar();
        u = units[unitId];
        if (u.registrar != msg.sender) revert NotUnitRegistrar(unitId);
    }

    function _requireLive(bytes32 unitId, Unit storage u) private view {
        if (u.state != State.FINANCED && u.state != State.POOLED) revert WrongState(unitId, State.FINANCED, u.state);
    }

    function _originatorPool(bytes32 poolId) private view returns (Pool storage p) {
        p = pools[poolId];
        if (p.originator == address(0)) revert UnknownPool(poolId);
        if (p.originator != msg.sender) revert NotOriginator(poolId, msg.sender);
    }

    /// @dev Order matters for the demo: ownership first, then encumbrance, then state.
    function _poolableUnit(bytes32 unitId) private view returns (Unit storage u) {
        u = units[unitId];
        if (u.owner != msg.sender) revert NotOwner(unitId, msg.sender);
        bytes32 live = livePoolOf[u.receivableKey];
        if (live != bytes32(0)) revert AlreadyEncumbered(u.receivableKey, live);
        if (u.state != State.FINANCED) revert WrongState(unitId, State.FINANCED, u.state);
    }

    function _checkAttestation(
        bytes32 poolId, bytes32 unitId, bytes32 receivableKey, bytes32 rulesHash, uint32 version, bytes calldata sig
    ) private view {
        if (sig.length != 65) revert BadAttestation(unitId);
        bytes32 r = bytes32(sig[0:32]);
        bytes32 s = bytes32(sig[32:64]);
        uint8 v = uint8(sig[64]);
        if (uint256(s) > HALF_N || (v != 27 && v != 28)) revert BadAttestation(unitId);
        address signer = ecrecover(attestationDigest(poolId, unitId, receivableKey, rulesHash, version), v, r, s);
        if (signer == address(0) || signer != engineSigner) revert BadAttestation(unitId);
    }

    function _enter(bytes32 poolId, Pool storage p, bytes32 unitId, Unit storage u, Op op) private {
        u.state = State.POOLED;
        u.currentPoolId = poolId;
        livePoolOf[u.receivableKey] = poolId;
        p.memberCount += 1;
        _commit(poolId, p, op, unitId);
    }

    function _exit(bytes32 poolId, Pool storage p, bytes32 unitId, Op op) private {
        Unit storage u = units[unitId];
        if (u.state != State.POOLED || u.currentPoolId != poolId) revert NotInPool(poolId, unitId);
        u.state = State.FINANCED;
        u.currentPoolId = bytes32(0);
        livePoolOf[u.receivableKey] = bytes32(0);
        p.memberCount -= 1;
        _commit(poolId, p, op, unitId);
    }

    /// @dev Settlement and default leave live membership; the caller sets the terminal state.
    function _leavePool(bytes32 unitId, Unit storage u, Op op) private {
        bytes32 poolId = u.currentPoolId;
        _exit(poolId, pools[poolId], unitId, op);
    }

    function _commit(bytes32 poolId, Pool storage p, Op op, bytes32 unitId) private {
        p.commitment = keccak256(abi.encodePacked(p.commitment, uint8(op), unitId));
        emit MembershipChanged(poolId, op, unitId, p.commitment, p.manifestVersion);
    }
}
