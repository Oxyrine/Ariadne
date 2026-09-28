// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import {Test, console} from "forge-std/Test.sol";
import {Vm} from "forge-std/Vm.sol";
import {AriadneLedger} from "../src/AriadneLedger.sol";

/// Shared fixture: two platforms, three financiers, one engine key.
abstract contract Fixture is Test {
    AriadneLedger ledger;
    uint256 constant ENGINE_PK = 0xE17;
    address p1 = makeAddr("platform1");
    address p2 = makeAddr("platform2");
    address A = makeAddr("financierA");
    address B = makeAddr("financierB");
    address C = makeAddr("financierC");
    bytes32 constant RULES = keccak256("rules-v1");

    function _deploy() internal {
        ledger = new AriadneLedger(vm.addr(ENGINE_PK));
        ledger.setRegistrar(p1, true);
        ledger.setRegistrar(p2, true);
    }

    function _key(uint256 i) internal pure returns (bytes32) { return keccak256(abi.encode("receivable", i)); }
    function _uid(address platform, uint256 i) internal pure returns (bytes32) { return keccak256(abi.encode(platform, i)); }

    function _register(address platform, uint256 i, bytes32 key) internal returns (bytes32 id) {
        id = _uid(platform, i);
        vm.prank(platform);
        ledger.registerUnit(id, key, uint64(block.timestamp), uint64(block.timestamp + 60 days), 1e7, bytes32("buyer"), bytes32("seller"));
    }

    function _financed(uint256 i, address owner) internal returns (bytes32 id) {
        id = _register(p1, i, _key(i));
        vm.prank(p1);
        ledger.recordFinancing(id, owner);
    }

    function _sign(uint256 pk, bytes32 poolId, bytes32 unitId, uint32 version) internal view returns (bytes memory) {
        (bytes32 key,,,,,,,,,,) = ledger.units(unitId);
        (, bytes32 rules,,,,,,) = ledger.pools(poolId);
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(pk, ledger.attestationDigest(poolId, unitId, key, rules, version));
        return abi.encodePacked(r, s, v);
    }

    function _attest(bytes32 poolId, bytes32 unitId, uint32 version) internal view returns (bytes memory) {
        return _sign(ENGINE_PK, poolId, unitId, version);
    }
}

contract AriadneLedgerTest is Fixture {
    bytes32 constant POOL1 = "pool-1";
    bytes32 constant POOL2 = "pool-2";

    function setUp() public {
        _deploy();
        vm.prank(B);
        ledger.createPool(POOL1, RULES, bytes32("groups"), bytes32("evidence"));
        vm.prank(B);
        ledger.createPool(POOL2, RULES, bytes32("groups"), bytes32("evidence"));
    }

    function _add(bytes32 poolId, bytes32 unitId) internal {
        bytes memory sig = _attest(poolId, unitId, 0);
        vm.prank(B);
        ledger.addToPool(poolId, unitId, sig);
    }

    // ------------------------------------------------------------ S6: stale owner

    function test_StaleOwnerCannotPool() public {
        bytes32 id = _financed(1, A);
        vm.prank(A);
        ledger.rediscount(id, B);
        vm.prank(B);
        ledger.rediscount(id, C);
        bytes memory sig = _attest(POOL1, id, 0);
        vm.prank(B);
        vm.expectRevert(abi.encodeWithSelector(AriadneLedger.NotOwner.selector, id, B));
        ledger.addToPool(POOL1, id, sig);
        (,, address owner,,,,,,,, uint32 transfers) = ledger.units(id);
        assertEq(owner, C);
        assertEq(transfers, 2);
    }

    // ---------------------------------------------------- S7a: duplicate receivable

    function test_SameInvoiceOnSecondPlatformReverts() public {
        _register(p1, 1, _key(1));
        bytes32 dup = _uid(p2, 99);
        vm.prank(p2);
        vm.expectRevert(abi.encodeWithSelector(AriadneLedger.DuplicateReceivable.selector, _key(1)));
        ledger.registerUnit(dup, _key(1), 0, 1, 1, 0, 0);
        (,,, AriadneLedger.State st,,,,,,,) = ledger.units(dup);
        assertEq(uint8(st), uint8(AriadneLedger.State.NONE));
    }

    // ------------------------------------------------------------ S7b: double pool

    function test_UnitLiveInOnePoolCannotEnterAnother() public {
        bytes32 id = _financed(1, B);
        _add(POOL1, id);
        bytes memory sig = _attest(POOL2, id, 0);
        vm.prank(B);
        vm.expectRevert(abi.encodeWithSelector(AriadneLedger.AlreadyEncumbered.selector, _key(1), POOL1));
        ledger.addToPool(POOL2, id, sig);
        assertEq(ledger.livePoolOf(_key(1)), POOL1);
    }

    function test_PooledUnitCannotBeRediscounted() public {
        bytes32 id = _financed(1, B);
        _add(POOL1, id);
        vm.prank(B);
        vm.expectRevert(abi.encodeWithSelector(AriadneLedger.AlreadyEncumbered.selector, _key(1), POOL1));
        ledger.rediscount(id, C);
    }

    // ------------------------------------------------------------- roles

    function test_RegistrarCannotTouchAnotherPlatformsUnit() public {
        bytes32 id = _financed(1, B);
        vm.prank(p2);
        vm.expectRevert(abi.encodeWithSelector(AriadneLedger.NotUnitRegistrar.selector, id));
        ledger.settle(id);
    }

    function test_NonRegistrarCannotRegister() public {
        vm.prank(A);
        vm.expectRevert(AriadneLedger.NotRegistrar.selector);
        ledger.registerUnit(bytes32("x"), bytes32("k"), 0, 1, 1, 0, 0);
    }

    function test_OnlyOriginatorFillsPool() public {
        bytes32 id = _financed(1, A);
        bytes memory sig = _attest(POOL1, id, 0);
        vm.prank(A);
        vm.expectRevert(abi.encodeWithSelector(AriadneLedger.NotOriginator.selector, POOL1, A));
        ledger.addToPool(POOL1, id, sig);
    }

    // ------------------------------------------------------------- attestations

    function test_AttestationFromWrongKeyReverts() public {
        bytes32 id = _financed(1, B);
        bytes memory sig = _sign(0xBAD, POOL1, id, 0);
        vm.prank(B);
        vm.expectRevert(abi.encodeWithSelector(AriadneLedger.BadAttestation.selector, id));
        ledger.addToPool(POOL1, id, sig);
    }

    function test_AttestationForOtherPoolOrVersionReverts() public {
        bytes32 id = _financed(1, B);
        bytes memory otherPool = _attest(POOL2, id, 0);
        vm.prank(B);
        vm.expectRevert(abi.encodeWithSelector(AriadneLedger.BadAttestation.selector, id));
        ledger.addToPool(POOL1, id, otherPool);
        bytes memory otherVersion = _attest(POOL1, id, 1);
        vm.prank(B);
        vm.expectRevert(abi.encodeWithSelector(AriadneLedger.BadAttestation.selector, id));
        ledger.addToPool(POOL1, id, otherVersion);
    }

    // ------------------------------------------------------------- lifecycle

    function test_SealThenSubstituteAndReplayCommitment() public {
        bytes32 a = _financed(1, B);
        bytes32 b = _financed(2, B);
        bytes32 c = _financed(3, B);
        _add(POOL1, a);
        _add(POOL1, b);
        vm.prank(B);
        ledger.sealPool(POOL1);

        // adding after seal is refused
        bytes memory late = _attest(POOL1, c, 1);
        vm.prank(B);
        vm.expectRevert(abi.encodeWithSelector(AriadneLedger.PoolAlreadySealed.selector, POOL1));
        ledger.addToPool(POOL1, c, late);

        // substitution needs an attestation at manifestVersion + 1
        bytes memory stale = _attest(POOL1, c, 1);
        vm.prank(B);
        vm.expectRevert(abi.encodeWithSelector(AriadneLedger.BadAttestation.selector, c));
        ledger.substitute(POOL1, a, c, stale);
        bytes memory sig = _attest(POOL1, c, 2);
        vm.prank(B);
        ledger.substitute(POOL1, a, c, sig);

        vm.prank(p1);
        ledger.settle(b);

        bytes32 h;
        h = keccak256(abi.encodePacked(h, uint8(AriadneLedger.Op.ADD), a));
        h = keccak256(abi.encodePacked(h, uint8(AriadneLedger.Op.ADD), b));
        h = keccak256(abi.encodePacked(h, uint8(AriadneLedger.Op.SUBSTITUTE_OUT), a));
        h = keccak256(abi.encodePacked(h, uint8(AriadneLedger.Op.SUBSTITUTE_IN), c));
        h = keccak256(abi.encodePacked(h, uint8(AriadneLedger.Op.SETTLED), b));
        (,,,, bytes32 commitment, uint32 members, uint32 version, bool sealed_) = ledger.pools(POOL1);
        assertEq(commitment, h);
        assertEq(members, 1);
        assertEq(version, 2);
        assertTrue(sealed_);
        (,,, AriadneLedger.State stA,,,,,,,) = ledger.units(a);
        assertEq(uint8(stA), uint8(AriadneLedger.State.FINANCED));
    }

    function test_SubstituteBeforeSealReverts() public {
        bytes32 a = _financed(1, B);
        bytes32 c = _financed(2, B);
        _add(POOL1, a);
        bytes memory sig = _attest(POOL1, c, 1);
        vm.prank(B);
        vm.expectRevert(abi.encodeWithSelector(AriadneLedger.PoolNotSealed.selector, POOL1));
        ledger.substitute(POOL1, a, c, sig);
    }

    function test_DefaultOnlyAfterDueDateAndIsTerminal() public {
        bytes32 id = _financed(1, B);
        _add(POOL1, id);
        vm.prank(p1);
        vm.expectRevert(abi.encodeWithSelector(AriadneLedger.NotYetDue.selector, id));
        ledger.markDefault(id);
        vm.warp(block.timestamp + 61 days);
        vm.prank(p1);
        ledger.markDefault(id);
        assertEq(ledger.livePoolOf(_key(1)), bytes32(0));
        vm.prank(p1);
        vm.expectRevert();
        ledger.settle(id);
    }
}

/// Drives random, bounded sequences of honest and hostile calls.
contract Handler is Fixture {
    uint256 constant N = 10;
    bytes32[2] public poolIds = [bytes32("hp-0"), bytes32("hp-1")];
    address[3] financiers;
    bytes32[] public ids;
    mapping(bytes32 => bool) public terminal;
    mapping(bytes32 => bytes32) public ghostCommitment;
    uint256 public unauthorisedSuccesses;
    uint256 public addsAfterSeal;
    uint256 public pooledOps; // successful membership events, to show the fuzzer reaches pooled states

    constructor(AriadneLedger l) {
        ledger = l;
        financiers = [A, B, C];
        vm.prank(A);
        ledger.createPool(poolIds[0], RULES, 0, 0);
        vm.prank(B);
        ledger.createPool(poolIds[1], RULES, 0, 0);
        for (uint256 i; i < N; i++) {
            address platform = i % 2 == 0 ? p1 : p2;
            ids.push(_register(platform, i, _key(i)));
            // pre-finance most units so short runs still reach pooled states
            if (i < 7) {
                vm.prank(platform);
                ledger.recordFinancing(ids[i], i % 2 == 0 ? A : B);
            }
        }
    }

    function unitCount() external pure returns (uint256) { return N; }

    function _unit(uint256 seed) internal view returns (bytes32 id, address registrar, address owner, AriadneLedger.State st) {
        id = ids[seed % N];
        (, registrar, owner, st,,,,,,,) = ledger.units(id);
    }

    function _pool(uint256 seed) internal view returns (bytes32 poolId, address originator, uint32 version, bool sealed_) {
        poolId = poolIds[seed % 2];
        (originator,,,,,, version, sealed_) = ledger.pools(poolId);
    }

    /// Record MembershipChanged logs into the ghost commitment.
    function _absorb(bool[2] memory wasSealed) internal {
        Vm.Log[] memory logs = vm.getRecordedLogs();
        bytes32 topic = keccak256("MembershipChanged(bytes32,uint8,bytes32,bytes32,uint32)");
        for (uint256 i; i < logs.length; i++) {
            if (logs[i].topics[0] != topic) continue;
            bytes32 poolId = logs[i].topics[1];
            bytes32 unitId = logs[i].topics[2];
            (uint8 op,,) = abi.decode(logs[i].data, (uint8, bytes32, uint32));
            pooledOps++;
            ghostCommitment[poolId] = keccak256(abi.encodePacked(ghostCommitment[poolId], op, unitId));
            uint256 idx = poolId == poolIds[0] ? 0 : 1;
            if (wasSealed[idx] && (op == uint8(AriadneLedger.Op.ADD) || op == uint8(AriadneLedger.Op.REMOVE))) addsAfterSeal++;
        }
    }

    function _sealedNow() internal view returns (bool[2] memory s) {
        (,,,,,,, s[0]) = ledger.pools(poolIds[0]);
        (,,,,,,, s[1]) = ledger.pools(poolIds[1]);
    }

    function _record() internal returns (bool[2] memory s) { s = _sealedNow(); vm.recordLogs(); }

    function _markTerminal() internal {
        for (uint256 i; i < N; i++) {
            (,,, AriadneLedger.State st,,,,,,,) = ledger.units(ids[i]);
            if (st == AriadneLedger.State.SETTLED || st == AriadneLedger.State.DEFAULTED) terminal[ids[i]] = true;
        }
    }

    // ------------------------------------------------------------ honest actions

    function finance(uint256 u, uint256 f) external {
        (bytes32 id, address registrar,, AriadneLedger.State st) = _unit(u);
        if (st != AriadneLedger.State.REGISTERED) return;
        vm.prank(registrar);
        ledger.recordFinancing(id, financiers[f % 3]);
    }

    function rediscount(uint256 u, uint256 f) external {
        (bytes32 id,, address owner, AriadneLedger.State st) = _unit(u);
        address to = financiers[f % 3];
        if (st != AriadneLedger.State.FINANCED || to == owner) return;
        vm.prank(owner);
        ledger.rediscount(id, to);
    }

    function add(uint256 p, uint256 u) external {
        (bytes32 poolId, address originator,,) = _pool(p);
        (bytes32 id,,,) = _unit(u);
        bytes memory sig = _attest(poolId, id, 0);
        bool[2] memory s = _record();
        vm.prank(originator);
        try ledger.addToPool(poolId, id, sig) {} catch {}
        _absorb(s);
    }

    function remove(uint256 p, uint256 u) external {
        (bytes32 poolId, address originator,,) = _pool(p);
        (bytes32 id,,,) = _unit(u);
        bool[2] memory s = _record();
        vm.prank(originator);
        try ledger.removeFromPool(poolId, id) {} catch {}
        _absorb(s);
    }

    function seal(uint256 p) external {
        (bytes32 poolId, address originator,,) = _pool(p);
        vm.prank(originator);
        try ledger.sealPool(poolId) {} catch {}
    }

    function substitute(uint256 p, uint256 out, uint256 inn) external {
        (bytes32 poolId, address originator, uint32 version,) = _pool(p);
        (bytes32 outId,,,) = _unit(out);
        (bytes32 inId,,,) = _unit(inn);
        bytes memory sig = _attest(poolId, inId, version + 1);
        bool[2] memory s = _record();
        vm.prank(originator);
        try ledger.substitute(poolId, outId, inId, sig) {} catch {}
        _absorb(s);
    }

    function settle(uint256 u) external {
        (bytes32 id, address registrar,,) = _unit(u);
        bool[2] memory s = _record();
        vm.prank(registrar);
        try ledger.settle(id) {} catch {}
        _absorb(s);
        _markTerminal();
    }

    function markDefault(uint256 u) external {
        (bytes32 id, address registrar,,) = _unit(u);
        vm.warp(block.timestamp + 61 days);
        bool[2] memory s = _record();
        vm.prank(registrar);
        try ledger.markDefault(id) {} catch {}
        _absorb(s);
        _markTerminal();
    }

    // ----------------------------------------------------------- hostile actions

    /// A non-owner tries to move or pool a unit; any success is a violation.
    function attackOwnership(uint256 u, uint256 f, uint256 p) external {
        (bytes32 id,, address owner,) = _unit(u);
        address attacker = financiers[f % 3];
        if (attacker == owner) return;
        vm.prank(attacker);
        try ledger.rediscount(id, attacker == A ? B : A) { unauthorisedSuccesses++; } catch {}
        (bytes32 poolId,,,) = _pool(p);
        bytes memory sig = _attest(poolId, id, 0);
        vm.prank(attacker);
        try ledger.addToPool(poolId, id, sig) { unauthorisedSuccesses++; } catch {}
    }

    /// The wrong registrar, or a financier, tries to settle or default a unit.
    function attackRegistrar(uint256 u, uint256 f) external {
        (bytes32 id, address registrar,,) = _unit(u);
        address wrong = registrar == p1 ? p2 : p1;
        vm.prank(wrong);
        try ledger.settle(id) { unauthorisedSuccesses++; } catch {}
        vm.prank(financiers[f % 3]);
        try ledger.markDefault(id) { unauthorisedSuccesses++; } catch {}
    }

    /// Terminal units must stay terminal.
    function attackTerminal(uint256 u) external {
        (bytes32 id, address registrar, address owner,) = _unit(u);
        if (!terminal[id]) return;
        vm.prank(registrar);
        try ledger.settle(id) { unauthorisedSuccesses++; } catch {}
        vm.prank(owner);
        try ledger.rediscount(id, owner == A ? B : A) { unauthorisedSuccesses++; } catch {}
    }
}

contract AriadneInvariants is Fixture {
    Handler handler;

    function setUp() public {
        _deploy();
        handler = new Handler(ledger);
        targetContract(address(handler));
        bytes4[] memory sel = new bytes4[](11);
        sel[0] = Handler.finance.selector;
        sel[1] = Handler.rediscount.selector;
        sel[2] = Handler.add.selector;
        sel[3] = Handler.remove.selector;
        sel[4] = Handler.seal.selector;
        sel[5] = Handler.substitute.selector;
        sel[6] = Handler.settle.selector;
        sel[7] = Handler.markDefault.selector;
        sel[8] = Handler.attackOwnership.selector;
        sel[9] = Handler.attackRegistrar.selector;
        sel[10] = Handler.attackTerminal.selector;
        targetSelector(FuzzSelector({addr: address(handler), selectors: sel}));
    }

    function _units() internal view returns (bytes32[] memory out) {
        uint256 n = handler.unitCount();
        out = new bytes32[](n);
        for (uint256 i; i < n; i++) out[i] = handler.ids(i);
    }

    /// 1. A FINANCED or POOLED unit always has exactly one non-zero owner.
    function invariant_LiveUnitsHaveOwner() public view {
        bytes32[] memory ids = _units();
        for (uint256 i; i < ids.length; i++) {
            (,, address owner, AriadneLedger.State st,,,,,,,) = ledger.units(ids[i]);
            if (st == AriadneLedger.State.FINANCED || st == AriadneLedger.State.POOLED) assertTrue(owner != address(0));
        }
    }

    /// 2. currentPoolId is non-zero iff POOLED.  3. livePoolOf matches it.
    function invariant_PoolIdIffPooled() public view {
        bytes32[] memory ids = _units();
        for (uint256 i; i < ids.length; i++) {
            (bytes32 key,,, AriadneLedger.State st, bytes32 poolId,,,,,,) = ledger.units(ids[i]);
            assertEq(st == AriadneLedger.State.POOLED, poolId != bytes32(0));
            assertEq(ledger.livePoolOf(key), poolId);
            assertEq(ledger.activeUnitOf(key), ids[i]);
        }
    }

    /// 4. Only the owner moves or pools a unit; only its registrar settles or defaults it.
    ///    5. SETTLED and DEFAULTED are terminal.
    function invariant_NoUnauthorisedMoves() public view {
        assertEq(handler.unauthorisedSuccesses(), 0);
        bytes32[] memory ids = _units();
        for (uint256 i; i < ids.length; i++) {
            if (!handler.terminal(ids[i])) continue;
            (,,, AriadneLedger.State st,,,,,,,) = ledger.units(ids[i]);
            assertTrue(st == AriadneLedger.State.SETTLED || st == AriadneLedger.State.DEFAULTED);
        }
    }

    /// 6. Stored commitment equals the event replay; memberCount equals live members.
    function invariant_CommitmentReplays() public view {
        bytes32[] memory ids = _units();
        for (uint256 p; p < 2; p++) {
            bytes32 poolId = handler.poolIds(p);
            (,,,, bytes32 commitment, uint32 members,,) = ledger.pools(poolId);
            assertEq(commitment, handler.ghostCommitment(poolId));
            uint256 live;
            for (uint256 i; i < ids.length; i++) {
                (,,,, bytes32 current,,,,,,) = ledger.units(ids[i]);
                if (current == poolId) live++;
            }
            assertEq(members, live);
        }
    }

    function afterInvariant() public view {
        console.log("membership events this run", handler.pooledOps());
    }

    /// 7. After sealing, membership changes only through substitute, settle or default.
    function invariant_SealedPoolsOnlyChangeThroughLifecycle() public view {
        assertEq(handler.addsAfterSeal(), 0);
    }
}
