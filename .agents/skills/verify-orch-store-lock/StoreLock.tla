----------------------------- MODULE StoreLock -----------------------------
(***************************************************************************)
(* How orch processes take and release the store lock, <store>/.orch.lock. *)
(* Source: pstack/skills/poteto-mode/scripts/orch/store.ts, acquireLock,   *)
(* its create and takeOver closures, the release closure it returns, and   *)
(* holderIsDead. Each action below is one filesystem call or one          *)
(* process.kill in that code, so TLC interleaves processes exactly where   *)
(* the kernel can. handle.close() is folded into the write because no     *)
(* other process can observe it.                                           *)
(*                                                                         *)
(* One process is one orch invocation: openStore's ensureLock shares one   *)
(* lockRequest per process, so a process acquires at most once.            *)
(***************************************************************************)
EXTENDS Naturals, FiniteSets

CONSTANTS
    Procs,       \* orch processes, as pid strings
    MaxCrashes   \* how many processes may die without running release()

VARIABLES
    lockIno,     \* inode at <store>/.orch.lock, 0 when there is no file
    lockText,    \* what readFile(path) returns: "" (created, not yet written) or a pid
    nextIno,     \* the inode the next successful open(path, "wx") creates
    pc,          \* each process's next step
    myIno,       \* the inode this process opened, which its writeFile goes to
    holder,      \* the holder string acquireLock read from the lock file
    alive,       \* whether process.kill(pid, 0) succeeds for this pid
    crashes

vars == <<lockIno, lockText, nextIno, pc, myIno, holder, alive, crashes>>

Unknown == "unknown"

Init ==
    /\ lockIno = 0
    /\ lockText = ""
    /\ nextIno = 1
    /\ pc = [p \in Procs |-> "Open"]
    /\ myIno = [p \in Procs |-> 0]
    /\ holder = [p \in Procs |-> Unknown]
    /\ alive = [p \in Procs |-> TRUE]
    /\ crashes = 0

Goto(p, step) == pc' = [pc EXCEPT ![p] = step]

\* create(): open(path, "wx") creates an empty file, or fails with EEXIST.
CreateLock(p) ==
    /\ lockIno' = nextIno
    /\ lockText' = ""
    /\ myIno' = [myIno EXCEPT ![p] = nextIno]
    /\ nextIno' = nextIno + 1

\* acquireLock's first create(). EEXIST goes on to read the holder.
Open(p) ==
    /\ pc[p] = "Open"
    /\ IF lockIno = 0
          THEN CreateLock(p) /\ Goto(p, "Write")
          ELSE Goto(p, "ReadHolder") /\ UNCHANGED <<lockIno, lockText, nextIno, myIno>>
    /\ UNCHANGED <<holder, alive, crashes>>

\* create(): handle.writeFile(`${pid}\n`). The handle names the inode this
\* process opened, so if that file was unlinked meanwhile, the write lands on
\* the orphaned inode and the path keeps whatever file is there now. Either
\* way create() resolves and acquireLock returns: the process now works.
Write(p) ==
    /\ pc[p] = "Write"
    /\ lockText' = IF lockIno = myIno[p] THEN p ELSE lockText
    /\ Goto(p, "Work")
    /\ UNCHANGED <<lockIno, nextIno, myIno, holder, alive, crashes>>

\* readFile(path).trim() || "unknown"; a failed read is also "unknown".
ReadHolder(p) ==
    /\ pc[p] = "ReadHolder"
    /\ holder' = [holder EXCEPT ![p] =
                    IF lockIno = 0 \/ lockText = "" THEN Unknown ELSE lockText]
    /\ Goto(p, "CheckHolder")
    /\ UNCHANGED <<lockIno, lockText, nextIno, myIno, alive, crashes>>

\* holderIsDead(holder): only a pid whose process.kill(pid, 0) fails with
\* ESRCH is dead. Otherwise acquireLock throws "store lock held by pid".
\* The force option, which takes over a live holder on purpose, is out of scope.
CheckHolder(p) ==
    /\ pc[p] = "CheckHolder"
    /\ IF holder[p] \in Procs /\ ~alive[holder[p]]
          THEN Goto(p, "Unlink")
          ELSE Goto(p, "Done")
    /\ UNCHANGED <<lockIno, lockText, nextIno, myIno, holder, alive, crashes>>

\* takeOver(): unlink(path), whichever file is there now. ENOENT throws.
Unlink(p) ==
    /\ pc[p] = "Unlink"
    /\ IF lockIno = 0
          THEN Goto(p, "Done") /\ UNCHANGED <<lockIno, lockText>>
          ELSE lockIno' = 0 /\ lockText' = "" /\ Goto(p, "ReOpen")
    /\ UNCHANGED <<nextIno, myIno, holder, alive, crashes>>

\* takeOver(): create() again. EEXIST throws "store lock held by pid".
ReOpen(p) ==
    /\ pc[p] = "ReOpen"
    /\ IF lockIno = 0
          THEN CreateLock(p) /\ Goto(p, "Write")
          ELSE Goto(p, "Done") /\ UNCHANGED <<lockIno, lockText, nextIno, myIno>>
    /\ UNCHANGED <<holder, alive, crashes>>

\* acquireLock has returned. The process writes the store until close().
Work(p) ==
    /\ pc[p] = "Work"
    /\ Goto(p, "ReleaseRead")
    /\ UNCHANGED <<lockIno, lockText, nextIno, myIno, holder, alive, crashes>>

\* release(): readFile(path).trim() === pid. ENOENT is ignored.
ReleaseRead(p) ==
    /\ pc[p] = "ReleaseRead"
    /\ IF lockIno # 0 /\ lockText = p
          THEN Goto(p, "ReleaseUnlink")
          ELSE Goto(p, "Done")
    /\ UNCHANGED <<lockIno, lockText, nextIno, myIno, holder, alive, crashes>>

\* release(): unlink(path), whichever file is there now. ENOENT is ignored.
ReleaseUnlink(p) ==
    /\ pc[p] = "ReleaseUnlink"
    /\ lockIno' = 0
    /\ lockText' = ""
    /\ Goto(p, "Done")
    /\ UNCHANGED <<nextIno, myIno, holder, alive, crashes>>

\* A process dies between any two steps and never runs release(). Its lock
\* file stays, and holderIsDead now reports its pid as dead.
Crash(p) ==
    /\ pc[p] \notin {"Done", "Dead"}
    /\ crashes < MaxCrashes
    /\ alive' = [alive EXCEPT ![p] = FALSE]
    /\ crashes' = crashes + 1
    /\ Goto(p, "Dead")
    /\ UNCHANGED <<lockIno, lockText, nextIno, myIno, holder>>

Step(p) ==
    \/ Open(p) \/ Write(p) \/ ReadHolder(p) \/ CheckHolder(p) \/ Unlink(p)
    \/ ReOpen(p) \/ Work(p) \/ ReleaseRead(p) \/ ReleaseUnlink(p)

\* Every process has released, given up, or died.
Finished ==
    /\ \A p \in Procs : pc[p] \in {"Done", "Dead"}
    /\ UNCHANGED vars

Next == (\E p \in Procs : Step(p) \/ Crash(p)) \/ Finished

Spec == Init /\ [][Next]_vars

TypeOK ==
    /\ lockIno \in 0..nextIno
    /\ lockText \in {""} \cup Procs
    /\ pc \in [Procs -> {"Open", "Write", "ReadHolder", "CheckHolder", "Unlink",
                         "ReOpen", "Work", "ReleaseRead", "ReleaseUnlink",
                         "Done", "Dead"}]
    /\ holder \in [Procs -> Procs \cup {Unknown}]
    /\ crashes \in 0..MaxCrashes

\* The promise: at most one live orch process writes the store at a time.
MutualExclusion ==
    Cardinality({p \in Procs : alive[p] /\ pc[p] = "Work"}) <= 1

=============================================================================
