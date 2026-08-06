#!/usr/bin/env python3
"""Verify file:line citations in the analysis documents against the actual sources."""
import re
import sys
from pathlib import Path

# This script lives in <repo>/scripts/, and the repo sits inside a west
# workspace alongside the zephyr source tree it cites:
#
#   <workspace>/            <- ROOT: what "zephyr/kernel/sched.c" resolves against
#   ├── zephyr/             <- the analysed source
#   └── <repo>/             <- DOCDIR: what "00-....md" resolves against
#       └── scripts/verify_refs.py
DOCDIR = Path(__file__).resolve().parent.parent
ROOT = DOCDIR.parent
DOCS = sorted(DOCDIR.glob("*.md"))

if not (ROOT / "zephyr").is_dir():
    sys.exit(
        f"zephyr 소스 트리를 찾을 수 없습니다: {ROOT / 'zephyr'}\n"
        "이 저장소가 west 워크스페이스 안에 있어야 소스 링크를 검사할 수 있습니다."
    )

# markdown link form: [label](path#L12) or [label](path#L12-L34)
LINK = re.compile(r"\[([^\]]+)\]\(([^)#]+)(?:#L(\d+)(?:-L(\d+))?)?\)")

# Expected content anchors: (file, line, substring that must appear on that line)
ANCHORS = [
    # --- 커널 코어 (1차 검증에서 이월) ---
    ("zephyr/kernel/include/ksched.h", 96, "z_sched_start"),
    ("zephyr/include/zephyr/kernel.h", 1397, "k_thread_start"),
    ("zephyr/include/zephyr/kernel.h", 92, "K_HIGHEST_THREAD_PRIO"),
    ("zephyr/include/zephyr/kernel_structs.h", 242, "extern struct z_kernel _kernel"),
    ("zephyr/include/zephyr/kernel_structs.h", 86, "_NON_PREEMPT_THRESHOLD"),
    ("zephyr/include/zephyr/kernel/thread.h", 57, "pended_on"),
    ("zephyr/kernel/sched.c", 35, "_sched_spinlock"),
    ("zephyr/kernel/sched.c", 40, "_thread_dummy"),
    ("zephyr/kernel/sched.c", 409, "pended_on == NULL"),
    ("zephyr/kernel/sched.c", 501, "blocking pend from ISR context"),
    ("zephyr/kernel/scheduler.c", 34, "sched_locked"),
    ("zephyr/kernel/thread.c", 898, "_THREAD_SLEEPING"),
    ("zephyr/kernel/thread.c", 1002, "may not be created in ISRs"),
    ("zephyr/kernel/include/kswap.h", 94, "swap_retval = -EAGAIN"),
    ("zephyr/kernel/include/kthread.h", 68, "_PREEMPT_THRESHOLD"),
    ("zephyr/kernel/Kconfig", 258, "default SCHED_SIMPLE"),
    ("zephyr/kernel/Kconfig", 310, "default WAITQ_SIMPLE"),
    ("zephyr/kernel/sleep.c", 65, "z_sched_unready_locked"),
    ("zephyr/kernel/timeslicing.c", 91, "slice_expired[cpu] ?"),
    # --- 타깃 구성 (Cortex-A53) 확정 근거 ---
    ("zephyr/arch/Kconfig", 50, "config ARM64"),
    ("zephyr/arch/Kconfig", 53, "select 64BIT"),
    ("zephyr/arch/Kconfig", 57, "select USE_SWITCH"),
    ("zephyr/arch/Kconfig", 60, "select ARCH_HAS_DIRECTED_IPIS"),
    ("zephyr/arch/arm64/core/Kconfig", 11, "select SCHED_IPI_SUPPORTED if SMP"),
    ("zephyr/arch/arm64/core/Kconfig", 40, "config CPU_CORTEX_A53"),
    ("zephyr/arch/arm/core/Kconfig", 30, "select SWAP_NONATOMIC"),
    ("zephyr/arch/riscv/Kconfig", 52, "select ARCH_HAS_CUSTOM_CURRENT_IMPL"),
    ("zephyr/kernel/Kconfig", 30, "default 16"),
    ("zephyr/kernel/Kconfig", 55, "default 15"),
    ("zephyr/kernel/Kconfig", 97, "default 0"),
    ("zephyr/kernel/Kconfig", 552, "default y"),
    ("zephyr/kernel/Kconfig", 746, "default y"),
    ("zephyr/kernel/Kconfig", 757, "default TIMEOUT_BACKEND_DLIST"),
    ("zephyr/kernel/Kconfig", 168, "select THREAD_ABORT_NEED_CLEANUP"),
    ("zephyr/kernel/sched.c", 308, "arch_sched_directed_ipi"),
    ("zephyr/include/zephyr/linker/section_tags.h", 67, "__incoherent __in_section_unique(cached)"),
    # --- 04 타임아웃과 시스템 클럭 ---
    ("zephyr/kernel/timeout.c", 20, "curr_tick"),
    ("zephyr/kernel/timeout.c", 39, "announcing_cpu = -1"),
    ("zephyr/kernel/timeout.c", 59, "INFLIGHT_SUPERSEDED_BIT 1UL"),
    ("zephyr/kernel/timeout.c", 184, "z_is_inactive_timeout(to)"),
    ("zephyr/kernel/timeout.c", 213, "this_cpu_announcing() ? 0 : 1"),
    ("zephyr/kernel/timeout.c", 221, "!any_cpu_announcing()"),
    ("zephyr/kernel/timeout.c", 253, "ret = -EAGAIN"),
    ("zephyr/kernel/timeout.c", 261, "inflight_mark_superseded()"),
    ("zephyr/kernel/timeout.c", 332, "sys_clock_announce_locked"),
    ("zephyr/kernel/timeout.c", 341, "announce_remaining += ticks"),
    ("zephyr/kernel/timeout.c", 399, "sys_clock_set_timeout(next_timeout(0), false)"),
    ("zephyr/kernel/timeout.c", 404, "z_time_slice()"),
    ("zephyr/kernel/timeout_list.h", 133, "t->dticks -= dt"),
    ("zephyr/kernel/timeout_minheap.h", 61, "k_panic()"),
    ("zephyr/kernel/timeout_minheap.h", 120, "ARG_UNUSED(dt)"),
    ("zephyr/kernel/timeout_wheel.h", 47, "_TIMEOUT_BACKEND_OWNS_ANNOUNCE 1"),
    ("zephyr/kernel/timer.c", 87, "z_timeout_inflight_superseded(t)"),
    ("zephyr/kernel/timer.c", 109, "K_TIMEOUT_ABS_TICKS"),
    ("zephyr/include/zephyr/drivers/timer/system_timer.h", 37, "SYS_CLOCK_MAX_WAIT (UINT32_MAX / 2)"),
    ("zephyr/drivers/timer/Kconfig.arm_arch", 10, "select TICKLESS_CAPABLE"),
    ("zephyr/kernel/include/ksched.h", 268, "z_try_abort_thread_timeout(thread)"),
    ("zephyr/kernel/sched.c", 777, "z_try_abort_thread_timeout(thread)"),
    # --- 05 SMP와 스핀락 ---
    ("zephyr/include/zephyr/spinlock.h", 201, "arch_irq_lock()"),
    ("zephyr/include/zephyr/spinlock.h", 216, "atomic_cas(&l->locked, 0, 1)"),
    ("zephyr/include/zephyr/spinlock.h", 92, "char dummy"),
    ("zephyr/include/zephyr/spinlock.h", 115, "MP_MAX_NUM_CPUS <= 8"),
    ("zephyr/include/zephyr/spinlock.h", 457, "define K_SPINLOCK(lck)"),
    ("zephyr/kernel/spinlock_validate.c", 10, "SPIN_CPU_ID_MASK (sizeof(void *) - 1)"),
    ("zephyr/kernel/spinlock_validate.c", 72, "cpu_id | (uintptr_t)_current"),
    ("zephyr/kernel/spinlock_validate.c", 158, "extra == 0"),
    ("zephyr/kernel/spinlock_validate.c", 162, "ifndef CONFIG_ARM64"),
    ("zephyr/kernel/smp/smp.c", 61, "atomic_cas(&global_lock, 0, 1)"),
    ("zephyr/kernel/smp/smp.c", 97, "volatile int i = 0; i < 1000"),
    ("zephyr/kernel/smp/smp.c", 114, "atomic_set(&ready_flag, 1)"),
    ("zephyr/kernel/smp/smp.c", 239, "atomic_set(&cpu_start_flag, 1)"),
    ("zephyr/kernel/smp/smp.c", 244, "arch_cpu_irqs_are_enabled()"),
    ("zephyr/kernel/smp/smp.c", 254, "arch_irq_lock()"),
    ("zephyr/kernel/smp/ipi.c", 23, "atomic_or(&_kernel.pending_ipi"),
    ("zephyr/kernel/smp/ipi.c", 32, "IPI_ALL_CPUS_MASK : 0"),
    ("zephyr/kernel/smp/ipi.c", 93, "atomic_and(&_kernel.pending_ipi"),
    ("zephyr/kernel/smp/ipi.c", 139, "~BIT(cpu_id)"),
    ("zephyr/kernel/smp/cpu_mask.c", 29, "z_is_thread_prevented_from_running(thread)"),
    ("zephyr/kernel/idle.c", 97, "__weak arch_spin_relax"),
    ("zephyr/arch/arm64/core/smp.c", 226, "arch_sched_directed_ipi"),
    ("zephyr/arch/arm64/core/smp.c", 284, "SGI_FPU_IPI"),
    ("zephyr/kernel/smp/Kconfig", 34, "select EVENTS"),
    ("zephyr/subsys/debug/Kconfig", 207, "config SPIN_VALIDATE"),
    ("zephyr/include/zephyr/kernel.h", 3961, "struct k_ipi_work {"),
    # --- 06 동기화 객체 ---
    ("zephyr/kernel/sem.c", 39, "static struct k_spinlock sem_lock"),
    ("zephyr/kernel/sem.c", 102, "z_sched_wake(&sem->wait_q, 0, NULL)"),
    ("zephyr/kernel/sem.c", 105, "sem->count != sem->limit"),
    ("zephyr/kernel/sem.c", 166, "-EAGAIN"),
    ("zephyr/kernel/mutex.c", 50, "static struct k_spinlock mutex_lock"),
    ("zephyr/kernel/mutex.c", 83, "CONFIG_PRIORITY_CEILING < K_LOWEST_THREAD_PRIO"),
    ("zephyr/kernel/mutex.c", 275, "adjust_owner_prio(mutex, mutex->owner_orig_prio)"),
    ("zephyr/kernel/mutex.c", 282, "LOCK_SCHED_SPINLOCK"),
    ("zephyr/kernel/mutex.c", 283, "z_unpend_first_thread_locked(&mutex->wait_q)"),
    ("zephyr/kernel/condvar.c", 124, "k_mutex_unlock(mutex)"),
    ("zephyr/kernel/condvar.c", 133, "k_mutex_lock(mutex, K_FOREVER)"),
    ("zephyr/kernel/events.c", 101, "K_EVENT_WAIT_ALL"),
    ("zephyr/kernel/events.c", 211, "z_sched_waitq_walk"),
    ("zephyr/kernel/events.c", 214, "data.events & ~data.clear_events"),
    ("zephyr/kernel/events.c", 331, "thread->events = events"),
    ("zephyr/kernel/poll.c", 35, "static struct k_spinlock poll_lock"),
    ("zephyr/kernel/poll.c", 327, "static _wait_q_t wait_q"),
    ("zephyr/kernel/poll.c", 474, "sys_dlist_get(events)"),
    ("zephyr/kernel/include/scheduler.h", 66, "indeterminate length"),
    ("zephyr/include/zephyr/kernel/thread.h", 290, "dual purpose"),
    ("zephyr/include/zephyr/kernel.h", 3572, "struct k_mutex {"),
    ("zephyr/include/zephyr/kernel.h", 3798, "struct k_sem {"),
    ("zephyr/kernel/Kconfig", 86, "default -128"),
    # --- 07 부팅과 초기화 ---
    ("zephyr/arch/arm64/core/reset.S", 123, "DAIFSet, #0xf"),
    ("zephyr/arch/arm64/core/reset.S", 134, "arm64_cpu_boot_params"),
    ("zephyr/arch/arm64/core/reset.S", 194, "z_prep_c"),
    ("zephyr/arch/arm64/core/reset.S", 220, "switch_el:"),
    ("zephyr/arch/arm64/core/reset.S", 229, "adr\tx0, switch_el"),
    ("zephyr/arch/arm64/core/reset.S", 257, "ret\tx25"),
    ("zephyr/arch/arm64/core/reset.c", 66, "EL3_TO_EL1_SKIP_EL2"),
    ("zephyr/arch/arm64/core/reset.c", 148, "z_arm64_el2_init()"),
    ("zephyr/arch/arm64/core/prep_c.c", 39, "write_tpidrro_el0"),
    ("zephyr/arch/arm64/core/prep_c.c", 44, "After bss clean"),
    ("zephyr/kernel/init.c", 102, "k_sched_lock()"),
    ("zephyr/kernel/init.c", 148, "K_KERNEL_STACK_ARRAY_DEFINE(z_interrupt_stacks"),
    ("zephyr/kernel/init.c", 227, "__init_end"),
    ("zephyr/kernel/init.c", 288, "z_sys_post_kernel = true"),
    ("zephyr/kernel/init.c", 352, "z_thread_essential_clear(&z_main_thread)"),
    ("zephyr/kernel/init.c", 461, "_kernel.ready_q.cache = &z_main_thread"),
    ("zephyr/kernel/init.c", 489, "z_swap_unlocked()"),
    ("zephyr/kernel/init.c", 547, "INIT_LEVEL_EARLY"),
    ("zephyr/kernel/init.c", 594, "switch_to_main_thread(prepare_multithreading())"),
    ("zephyr/include/zephyr/init.h", 111, "Z_INIT_ENTRY_SECTION"),
    ("zephyr/include/zephyr/kernel.h", 876, "k_is_pre_kernel"),
    ("zephyr/arch/arm64/include/kernel_arch_func.h", 33, "arch_kernel_init"),
    # --- 08 계층과 의존 구조 ---
    ("zephyr/CMakeLists.txt", 128, "zephyr_include_directories("),
    ("zephyr/kernel/CMakeLists.txt", 188, "target_include_directories(kernel PRIVATE"),
    ("zephyr/arch/CMakeLists.txt", 8, "include_directories("),
    ("zephyr/cmake/modules/extensions.cmake", 1994, "kernel/include"),
    # --- 09 확장 메커니즘 ---
    ("zephyr/kernel/main_weak.c", 8, "Linkers may treat weak functions differently"),
    ("zephyr/kernel/main_weak.c", 24, "int __weak main(void)"),
    ("zephyr/kernel/fatal.c", 37, "__weak void k_sys_fatal_error_handler"),
    ("zephyr/kernel/init.c", 497, "z_early_rand_get"),
    ("zephyr/include/zephyr/platform/hooks.h", 22, "CONFIG_SOC_EARLY_RESET_HOOK"),
    ("zephyr/include/zephyr/platform/hooks.h", 33, "define soc_early_reset_hook()"),
    ("zephyr/include/zephyr/device.h", 1371, "STRUCT_SECTION_ITERABLE"),
    ("zephyr/include/zephyr/sys/iterable_sections.h", 244, "define STRUCT_SECTION_ITERABLE"),
    ("zephyr/kernel/timeout.c", 296, "EXPORT_SYMBOL(z_timeout_remaining)"),
    ("zephyr/kernel/spinlock_validate.c", 38, "EXPORT_SYMBOL(z_spin_lock_valid)"),
    ("zephyr/subsys/portability/posix/options/CMakeLists.txt", 187, "zephyr_library_include_directories("),
    ("zephyr/subsys/tracing/CMakeLists.txt", 65, "zephyr_include_directories_ifdef("),
    ("zephyr/subsys/portability/posix/options/semaphore.c", 16, "#include <wait_q.h>"),
    # --- 3차 독립 검증(46개 명제)에서 정정된 서술의 근거 ---
    ("zephyr/include/zephyr/linker/common-rom/common-rom-kernel-devices.ld", 14, "CREATE_OBJ_LEVEL(init, EARLY)"),
    ("zephyr/include/zephyr/linker/linker-defs.h", 70, "define CREATE_OBJ_LEVEL"),
    ("zephyr/kernel/timeslicing.c", 194, "z_is_idle_thread_object(next)"),
    ("zephyr/kernel/include/priority_q.h", 137, "ifndef CONFIG_SMP"),
    ("zephyr/kernel/scheduler.c", 116, "post_func"),
]

def resolve(rel):
    """Links use one of two bases:
      - workspace root  (analysis docs -> zephyr sources, and doc-to-doc links
        written as '1.zephyr분석/xx.md' so they click through in the IDE)
      - repository root (README.md -> sibling docs, so they work on GitHub)
    Accept either.
    """
    for base in (ROOT, DOCDIR):
        p = base / rel
        if p.exists():
            return p
    return None


def lines_of(rel):
    p = resolve(rel)
    if p is None:
        return None
    return p.read_text(encoding="utf-8", errors="replace").splitlines()

fail = 0
checked = 0

print("=== 1. 링크 대상 파일 및 행 범위 유효성 ===")
seen = set()
for doc in DOCS:
    for label, rel, s, e in LINK.findall(doc.read_text(encoding="utf-8")):
        if rel.startswith(("http:", "https:", "#")):
            continue
        key = (rel, s, e)
        if key in seen:
            continue
        seen.add(key)
        src = lines_of(rel)
        checked += 1
        if src is None:
            print(f"  FAIL 파일 없음: {rel}  ({doc.name} / {label})")
            fail += 1
            continue
        n = len(src)
        for num in (s, e):
            if num and int(num) > n:
                print(f"  FAIL 행 초과: {rel}#L{num} (총 {n}행)  ({doc.name} / {label})")
                fail += 1
        if s and e and int(e) < int(s):
            print(f"  FAIL 범위 역전: {rel}#L{s}-L{e}  ({doc.name} / {label})")
            fail += 1
print(f"  검사한 고유 링크: {checked}건")

print("\n=== 2. 핵심 인용 행의 내용 일치 ===")
for rel, line, needle in ANCHORS:
    src = lines_of(rel)
    if src is None:
        print(f"  FAIL 파일 없음: {rel}")
        fail += 1
        continue
    if line > len(src):
        print(f"  FAIL 행 초과: {rel}:{line}")
        fail += 1
        continue
    actual = src[line - 1]
    if needle in actual:
        print(f"  OK   {rel}:{line}")
    else:
        print(f"  FAIL {rel}:{line}  기대 {needle!r}  실제 {actual.strip()!r}")
        fail += 1

print("\n=== 3. 절 참조 (§) 유효성 ===")
# 문서들이 서로의 절을 "04-....md §6.3", "02 §7", "본 문서 §4.2" 처럼 가리킨다.
# 대상 문서에 그 절이 실제로 있는지 확인한다. 절 번호는 문서를 고치면 쉽게
# 어긋나는데 링크 검사로는 잡히지 않으므로 별도 검사가 필요하다.
#
# 귀속 규칙(휴리스틱): 한 줄 안에서 § 앞 PROXIMITY자 이내에 문서 지시자가
# 있으면 그 문서, 없으면 자기 문서로 본다. "본 문서" 가 바로 앞에 오면 항상
# 자기 문서다. 표 한 줄에 여러 참조가 섞여도 대체로 맞지만 완벽하지는 않다.
PROXIMITY = 40

doc_text = {p.name: p.read_text(encoding="utf-8") for p in DOCS}
by_num = {n[:2]: n for n in doc_text if re.match(r"^[0-9]{2}-", n)}

doc_sections = {}
for _name, _text in doc_text.items():
    _s = set()
    for _m in re.finditer(r"^#{2,4}\s+([0-9]+(?:[.\-][0-9]+)*)\.?\s", _text, re.M):
        _num = _m.group(1)
        _s.add(_num)
        _parts = _num.replace("-", ".").split(".")
        for _i in range(1, len(_parts)):
            _s.add(".".join(_parts[:_i]))   # "4.2" 가 있으면 "4" 도 유효
    doc_sections[_name] = _s

DOCREF = re.compile(
    r"(?:(?P<full>[0-9]{2}-[^\s`\)\]]+?\.md)"
    r"|(?<![/\w])(?P<meta>(?:TODO|README)\.md)"
    r"|(?<![0-9.\w])(?P<num>[0-9]{2})(?=\s*§))"
)
SECREF = re.compile(r"§\s*([0-9]+(?:[.\-][0-9]+)*)")

sec_checked = 0
for name, text in doc_text.items():
    for lineno, line in enumerate(text.splitlines(), 1):
        refs = []
        for m in DOCREF.finditer(line):
            t = m.group("full") or m.group("meta") or by_num.get(m.group("num"))
            if t:
                refs.append((m.end(), t))
        for m in SECREF.finditer(line):
            sec = m.group(1)
            sec_checked += 1
            near = [t for (end, t) in refs if 0 <= m.start() - end <= PROXIMITY]
            target = near[-1] if near else name
            if "본 문서" in line[max(0, m.start() - 12):m.start()]:
                target = name
            if target not in doc_text:
                print(f"  FAIL {name}:{lineno} 대상 문서 없음: {target}")
                fail += 1
            elif sec not in doc_sections[target]:
                print(f"  FAIL {name}:{lineno} → {target} §{sec} 없음")
                print(f"       | {line.strip()[:90]}")
                fail += 1
print(f"  검사한 절 참조: {sec_checked}건")

print(f"\n결과: {'모두 통과' if fail == 0 else str(fail) + '건 실패'}")
sys.exit(1 if fail else 0)
