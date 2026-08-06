# SMP와 스핀락

분석 대상: [zephyr/include/zephyr/spinlock.h](zephyr/include/zephyr/spinlock.h), [zephyr/kernel/spinlock_validate.c](zephyr/kernel/spinlock_validate.c), [zephyr/kernel/smp/smp.c](zephyr/kernel/smp/smp.c), [zephyr/kernel/smp/ipi.c](zephyr/kernel/smp/ipi.c), [zephyr/kernel/smp/cpu_mask.c](zephyr/kernel/smp/cpu_mask.c), [zephyr/kernel/include/ipi.h](zephyr/kernel/include/ipi.h), [zephyr/arch/arm64/core/smp.c](zephyr/arch/arm64/core/smp.c)

> **타깃 적용 (`qemu_cortex_a53` SMP)** — 전체 구성은 [00-분석-개요.md](1.zephyr분석/00-분석-개요.md) §4 참조
>
> | 본 문서 항목 | 이 타깃에서 |
> |---|---|
> | §2 스핀락 구현 | **기본 원자 변수 방식.** `TICKET_SPINLOCKS`는 EXPERIMENTAL이며 기본 n |
> | §3 `SPIN_VALIDATE` | **활성 추정.** `default y if !FLASH \|\| FLASH_SIZE > 32`, CPU 2개·64비트로 의존성 충족 ([subsys/debug/Kconfig:207-212](zephyr/subsys/debug/Kconfig#L207-L212)). (확신도: medium — `CONFIG_FLASH` 값을 빌드로 확인하지 못함) |
> | §3 `SPIN_LOCK_TIME_LIMIT` | **비활성.** 기본 0 |
> | §6.3 `IPI_OPTIMIZE` | **비활성** (기본 n) → `ipi_mask_create()`가 항상 `IPI_ALL_CPUS_MASK` 반환 |
> | §6.4 IPI 전달 방식 | **directed.** `ARCH_HAS_DIRECTED_IPIS=y` → GIC SGI0 |
> | §7 CPU 친화도 | **비활성.** `SCHED_CPU_MASK=n` → `k_thread_cpu_mask_*()` 미제공 |
> | `SCHED_IPI_CASCADE` | **비활성.** `SCHED_CPU_MASK` 의존 |
> | §9 `arch_spin_relax` | **weak 기본 구현 사용.** arm64 전용 버전은 `CONFIG_FPU_SHARING` 안에 있고 해당 옵션이 기본 n |
> | `KERNEL_COHERENCE` | **비활성** → `z_spin_lock_mem_coherent()` 검사 없음 |
> | CPU 수 | 2 (`MP_MAX_NUM_CPUS=2`) |

---

## 1. 구성 요소

| 파일 | 역할 |
|---|---|
| `include/zephyr/spinlock.h` | `k_spinlock` **구현 전체**. 전부 `static ALWAYS_INLINE` |
| `kernel/spinlock_validate.c` | `CONFIG_SPIN_VALIDATE` 검증 프레임워크 |
| `kernel/smp/smp.c` | 보조 CPU 부팅, 글로벌 락, `_current` 조회 |
| `kernel/smp/ipi.c` | IPI 플래그·발송·수신, `k_ipi_work` |
| `kernel/smp/cpu_mask.c` | CPU 친화도 API |
| `arch/*/core/smp.c` | `arch_sched_*_ipi()`, `arch_spin_relax()` 등 아키텍처 구현 |

스핀락에 `.c` 파일이 없다는 점이 특징입니다. 락 획득/해제가 인터럽트 마스킹과 원자 연산 몇 줄로 끝나므로, 호출 지점마다 인라인 전개되는 편이 유리하기 때문입니다.

---

## 2. `k_spinlock`

### 2.1 구조체 — 구성에 따라 크기가 0이 될 수 있음

[spinlock.h:45-97](zephyr/include/zephyr/spinlock.h#L45-L97)

```c
struct k_spinlock {
#ifdef CONFIG_SMP
#ifdef CONFIG_TICKET_SPINLOCKS
	atomic_t owner;
	atomic_t tail;
#else
	atomic_t locked;
#endif
#endif
#ifdef CONFIG_SPIN_VALIDATE
	uintptr_t thread_cpu;
#ifdef CONFIG_SPIN_LOCK_TIME_LIMIT
	uint32_t lock_time;
#endif
#endif
#if defined(CONFIG_NONZERO_SPINLOCK_SIZE) && !defined(CONFIG_SMP) && !defined(CONFIG_SPIN_VALIDATE)
	char dummy;
#endif
};
```

UP + 검증 비활성 조합에서는 **멤버가 하나도 남지 않습니다.** 그때 `dummy`가 붙는 이유가 주석에 있습니다([spinlock.h:82-91](zephyr/include/zephyr/spinlock.h#L82-L91)): 빈 구조체의 크기가 **C에서는 0, C++에서는 1**이라, `k_msgq` 같은 다른 구조체에 임베드될 때 두 언어가 **후속 멤버의 오프셋을 다르게 계산**합니다. 언어 간 ABI 불일치를 막기 위한 패딩입니다.

이 타깃(SMP=y)에서는 `atomic_t locked` 하나가 실체입니다.

### 2.2 획득 — 순서가 핵심

[spinlock.h:192-226](zephyr/include/zephyr/spinlock.h#L192-L226)

```c
static ALWAYS_INLINE k_spinlock_key_t k_spin_lock(struct k_spinlock *l)
{
	k_spinlock_key_t k;

	k.key = arch_irq_lock();          /* ① 먼저 인터럽트 차단 */

	z_spinlock_validate_pre(l);
#ifdef CONFIG_SMP
	while (!atomic_cas(&l->locked, 0, 1)) {     /* ② 그 다음 스핀 */
		do {
			arch_spin_relax();
		} while (atomic_get(&l->locked) != 0);
	}
#endif
	z_spinlock_validate_post(l);
	return k;
}
```

**① 인터럽트 차단이 먼저**여야 하는 이유는 API 계약 때문입니다. 문서화된 보장은 "반환 시점부터 `k_spin_unlock()`까지 **자기 CPU에서 중단되거나 선점되지 않는다**"입니다([spinlock.h:165-172](zephyr/include/zephyr/spinlock.h#L165-L172)). 락을 먼저 잡고 인터럽트를 나중에 막으면 그 사이에 ISR이 끼어들고, 그 ISR이 같은 락을 잡으려 하면 **자기 자신을 기다리는 데드락**이 됩니다.

주석은 SMP에서 `irq_lock()`이 실제로는 **글로벌 스핀락의 래퍼**이므로 여기서는 반드시 아키텍처 원시 함수 `arch_irq_lock()`을 써야 한다고 명시합니다([spinlock.h:197-200](zephyr/include/zephyr/spinlock.h#L197-L200)). §4에서 다룹니다.

**② 스핀 루프가 이중**인 점도 의도적입니다. 바깥은 `atomic_cas`(쓰기를 동반해 캐시 라인을 배타 상태로 만듦), 안쪽은 `atomic_get`(읽기만)입니다. 교과서적인 **test-and-test-and-set**으로, 경합 시 캐시 라인 핑퐁을 줄입니다. (확신도: high — 구조가 해당 패턴과 정확히 일치)

UP에서는 `#ifdef CONFIG_SMP` 블록이 통째로 사라져 **인터럽트 마스킹만 남습니다.** 헤더 주석이 이 축약을 명시적으로 허용합니다([spinlock.h:179-183](zephyr/include/zephyr/spinlock.h#L179-L183)).

> **재귀 불가**: 스핀락은 중첩(서로 다른 락)은 되지만 재귀(같은 락)는 데드락입니다([spinlock.h:174-177](zephyr/include/zephyr/spinlock.h#L174-L177)).
>
> **컨텍스트 스위치 중 보유 금지**: `@warning Holding a spinlock when a context switch occurs is illegal.` — §3의 `z_assert_can_swap()`이 이를 검사합니다.

### 2.3 티켓 스핀락 — 공정성 옵션

`CONFIG_TICKET_SPINLOCKS`를 켜면 `owner`/`tail` 두 원자 변수로 **FIFO 순서**를 보장합니다([spinlock.h:50-63](zephyr/include/zephyr/spinlock.h#L50-L63)). 기본 구현은 공정성이 없어 "한 CPU가 매번 경합에서 이겨 라이브락에 빠지는 것도 가능"하다고 Kconfig가 인정합니다([smp/Kconfig:163-174](zephyr/kernel/smp/Kconfig#L163-L174)). 메모리 사용이 늘고 EXPERIMENTAL이라 기본은 꺼져 있습니다.

`k_spin_trylock()`의 티켓 버전에는 이론적 결함이 주석으로 남아 있습니다([spinlock.h:249-266](zephyr/include/zephyr/spinlock.h#L249-L266)) — `atomic_get`과 `atomic_cas` 사이에 티켓 카운터가 한 바퀴 돌면 이미 잠긴 락을 잠글 수 있습니다. 다만 그러려면 `0xffff...ffff`개의 CPU가 필요하거나 그 횟수만큼 잠금/해제가 **인터럽트가 차단된 수 명령어 구간 안에** 일어나야 하므로 실제로는 재현 불가로 평가합니다.

### 2.4 해제와 `k_spin_release`

[spinlock.h:312-344](zephyr/include/zephyr/spinlock.h#L312-L344), [spinlock.h:378-391](zephyr/include/zephyr/spinlock.h#L378-L391)

`atomic_clear()`를 쓰는 이유가 솔직하게 적혀 있습니다([spinlock.h:333-339](zephyr/include/zephyr/spinlock.h#L333-L339)): 락을 쥐고 있으니 경쟁이 없어 원자적 교환이 **엄밀히는 불필요**하지만, 일부 아키텍처가 메모리 배리어를 필요로 하는데 **Zephyr에 그 용도의 프레임워크가 없어서** 원자 연산으로 대신한다는 것입니다.

`k_spin_release()`는 **락만 놓고 인터럽트는 계속 차단**한 채 둡니다. 컨텍스트 스위치 경로 전용입니다 — `z_pend_curr()`가 호출자의 락을 놓으면서도 스위치에 도달할 때까지 인터럽트를 막아두는 데 씁니다([sched.c:520](zephyr/kernel/sched.c#L520), `02-스케줄러.md` §8).

### 2.5 `K_SPINLOCK` 매크로

[spinlock.h:457-459](zephyr/include/zephyr/spinlock.h#L457-L459)

```c
#define K_SPINLOCK(lck)                                                          \
	for (k_spinlock_key_t __i K_SPINLOCK_ONEXIT = {}, __key = k_spin_lock(lck); \
	     !__i.key; k_spin_unlock((lck), __key), __i.key = 1)
```

**정확히 한 번 실행되는 for 루프**입니다. 블록을 벗어날 때 자동으로 해제되므로 해제 누락을 구조적으로 막습니다.

함정은 `break`/`goto`/`return`으로 빠져나가면 **해제되지 않는다**는 것입니다. 그래서 `K_SPINLOCK_BREAK`(= `continue`)를 제공하고, `CONFIG_SPIN_VALIDATE` + GCC 환경에서는 `__attribute__((cleanup))`으로 잘못된 이탈을 런타임에 잡습니다([spinlock.h:393-402](zephyr/include/zephyr/spinlock.h#L393-L402)).

---

## 3. 스핀락 검증 — `CONFIG_SPIN_VALIDATE`

[spinlock_validate.c](zephyr/kernel/spinlock_validate.c)

### 3.1 소유자 표현 — 포인터 하위 비트 재활용

```c
#define SPIN_CPU_ID_MASK (sizeof(void *) - 1)

void z_spin_lock_set_owner(struct k_spinlock *l)
{
	uint8_t cpu_id = _current_cpu->id;
	l->thread_cpu = cpu_id | (uintptr_t)_current;
	...
}
```
[spinlock_validate.c:10](zephyr/kernel/spinlock_validate.c#L10), [:68-78](zephyr/kernel/spinlock_validate.c#L68-L78)

스레드 포인터는 정렬되어 있으므로 하위 2~3비트가 비어 있습니다. 그 자리에 CPU id를 얹어 **워드 하나에 "누가, 어느 CPU에서" 잡았는지**를 담습니다.

> `04-타임아웃과-시스템-클럭.md` §6.2의 `inflight_timeout`이 같은 기법을 씁니다. 포인터 정렬 여유 비트에 부가 정보를 얹는 것이 이 커널의 반복되는 관용구입니다.

대가는 **CPU 개수 상한**이며, 빌드 시각에 강제됩니다([spinlock.h:114-118](zephyr/include/zephyr/spinlock.h#L114-L118)).

```c
#if defined(CONFIG_64BIT)
BUILD_ASSERT(CONFIG_MP_MAX_NUM_CPUS <= 8, "Too many CPUs for mask");
#else
BUILD_ASSERT(CONFIG_MP_MAX_NUM_CPUS <= 4, "Too many CPUs for mask");
#endif
```

Kconfig의 `depends on MP_MAX_NUM_CPUS <= 4 || (64BIT && MP_MAX_NUM_CPUS <= 8)`도 같은 제약입니다([subsys/debug/Kconfig:209-210](zephyr/subsys/debug/Kconfig#L209-L210)).

### 3.2 추적 대상 두 가지

```c
static struct k_spinlock *z_held_spinlock[CONFIG_MP_MAX_NUM_CPUS];  /* 최외곽 락 */
static int z_held_spinlock_count[CONFIG_MP_MAX_NUM_CPUS];           /* 총 보유 수 */
```
[spinlock_validate.c:12-20](zephyr/kernel/spinlock_validate.c#L12-L20)

포인터는 **set-if-NULL / clear-if-matches** 방식이라 최외곽 락만 남고, 카운트는 중첩과 무관하게 매 획득/해제마다 증감합니다. 이 둘을 나눈 이유가 §3.3에서 드러납니다.

### 3.3 컨텍스트 스위치 검사 — `z_assert_can_swap()`

[spinlock_validate.c:134-173](zephyr/kernel/spinlock_validate.c#L134-L173). `do_swap()`과 `z_swap_irqlock()`이 스케줄러에 넘기기 직전에 호출합니다([kswap.h:86-88](zephyr/kernel/include/kswap.h#L86-L88)).

```c
int extra = (swap_lock != NULL) ? count - 1 : count;

__ASSERT(extra >= 0, "swap_lock %p is not tracked in the spinlock hold count!", swap_lock);
__ASSERT(held == NULL || held == swap_lock, "Context switching while holding spinlock %p!", held);
__ASSERT(extra == 0, "Context switching while holding %d extra spinlock(s)!", extra);
```

스위치와 함께 해제될 락(`swap_lock`, 보통 `_sched_spinlock`) **하나만** 보유가 허용되고, 나머지는 전부 버그입니다.

세 번째 어서션이 왜 따로 필요한지가 주석에 있습니다([spinlock_validate.c:155-157](zephyr/kernel/spinlock_validate.c#L155-L157)):

> `lock(A); lock(B); unlock(A); z_swap();` — A가 해제될 때 슬롯은 비지만 B가 여전히 잡혀 있어 카운트는 1로 남습니다.

포인터만 봤다면 `held == NULL`이라 통과했을 상황입니다. **포인터와 카운트를 함께 추적하는 이유**가 바로 이 케이스입니다.

네 번째 검사는 인터럽트 상태입니다 — 중첩된 `irq_lock()` 임계 구역에서 스위치하면 **호출 스택 상위의 락을 깨는 것**이므로 금지입니다. 다만 ARM64에서는 **비활성**입니다([spinlock_validate.c:162-172](zephyr/kernel/spinlock_validate.c#L162-L172)):

> 예외 컨텍스트에서 FP/SIMD를 쓰면 락 보유 없이도 IRQ가 마스킹된 채 남을 수 있음 (이슈 #94285)

본 분석 타깃이 ARM64이므로 **이 검사는 적용되지 않습니다.**

### 3.4 비정상 종료 처리

- `z_spin_unlock_valid()`는 **ISR이 `_current`를 abort한 엣지 케이스**를 별도 처리합니다([spinlock_validate.c:51-57](zephyr/kernel/spinlock_validate.c#L51-L57)). `_current`가 이미 더미로 바뀌었으므로 소유자 대조가 성립하지 않아, 카운트만 줄이고 통과시킵니다. (`03-스레드-생명주기.md` §7.3의 더미화)
- `z_spin_validate_reset()`은 죽은 스레드가 쥐고 있던 락을 **포기 처리**합니다([spinlock_validate.c:90-102](zephyr/kernel/spinlock_validate.c#L90-L102)). 그 스레드는 재개되지 않으므로 해제될 일이 없습니다.
- `z_spinlock_abort_sentinel`은 ztest 하네스가 "의도된 패닉 후 락을 쥔 채 강제 abort한다"고 표시하는 값입니다([spinlock_validate.c:22-25](zephyr/kernel/spinlock_validate.c#L22-L25)). 진짜 버그는 계속 어서션에 걸리게 하면서 테스트만 통과시키는 장치입니다.
- `z_spin_lock_transfer_owner()`는 스위치로 락 소유권이 새 스레드에 넘어갈 때 소유자만 갱신하고 **추적 배열은 건드리지 않습니다**([spinlock_validate.c:81-88](zephyr/kernel/spinlock_validate.c#L81-L88)). CPU 기준 보유 수는 변하지 않았기 때문입니다.

---

## 4. 글로벌 락 — SMP에서의 `irq_lock()`

[smp.c:56-90](zephyr/kernel/smp/smp.c#L56-L90)

SMP에서 `irq_lock()`은 이름과 달리 **전역 스핀락**입니다.

```c
unsigned int z_smp_global_lock(void)
{
	unsigned int key = arch_irq_lock();

	if (!_current->base.global_lock_count) {
		while (!atomic_cas(&global_lock, 0, 1)) {
			arch_spin_relax();
		}
	}
	_current->base.global_lock_count++;
	return key;
}
```

특징:

- **스레드별 재귀 카운트**(`base.global_lock_count`, [thread.h:107-108](zephyr/include/zephyr/kernel/thread.h#L107-L108))를 둬서 중첩 호출을 허용합니다. `k_spinlock`이 재귀 불가인 것과 대조됩니다.
- 카운트가 스레드에 붙어 있으므로 **컨텍스트 스위치를 넘어 유지**됩니다. `do_swap()`이 새 스레드의 카운트에 맞춰 락 상태를 복원하는 이유입니다([kswap.h:25-32](zephyr/kernel/include/kswap.h#L25-L32) 주석).
- `z_smp_release_global_lock()`은 스위치 시점에 호출되어, 들어오는 스레드가 글로벌 락을 원하지 않으면 풀어줍니다([smp.c:85-90](zephyr/kernel/smp/smp.c#L85-L90)).

이 구조 때문에 §2.2에서 `k_spin_lock()`이 `irq_lock()`이 아니라 `arch_irq_lock()`을 써야 합니다. 그러지 않으면 모든 스핀락 획득이 전역 락 경합을 유발합니다.

---

## 5. `_current`의 SMP 구현

[smp.c:242-259](zephyr/kernel/smp/smp.c#L242-L259)

```c
bool z_smp_cpu_mobile(void)
{
	return !arch_is_in_isr() && arch_cpu_irqs_are_enabled();
}

__attribute_const__ struct k_thread *z_smp_current_get(void)
{
	unsigned int key = arch_irq_lock();
	struct k_thread *t = _current_cpu->current;

	arch_irq_unlock(key);
	return t;
}
```

`_current`를 읽는 데 **인터럽트를 잠가야 하는 이유**가 주석에 있습니다: `_current`는 `_current_cpu`에서 읽는 필드인데, **읽기 전에 선점이 끼어들 수 있습니다.** 즉 `_current_cpu`를 확정한 직후 다른 CPU로 옮겨가면 엉뚱한 CPU의 `current`를 읽게 됩니다.

`z_smp_cpu_mobile()`은 "지금 이 실행 문맥이 다른 CPU로 옮겨갈 수 있는가"를 답하며, `_current_cpu` 매크로의 어서션에 쓰입니다([kernel_structs.h:252-253](zephyr/include/zephyr/kernel_structs.h#L252-L253), `01-커널-코어-자료구조.md` §3).

---

## 6. IPI (Inter-Processor Interrupt)

### 6.1 지연 발송 구조

IPI는 **즉시 보내지 않고 비트맵에 모았다가 한 번에 발송**합니다. 저장소는 `_kernel.pending_ipi`(atomic)입니다([kernel_structs.h:234-237](zephyr/include/zephyr/kernel_structs.h#L234-L237)).

```
스케줄러 조작 (락 보유)          →  flag_ipi(mask)      →  pending_ipi |= mask
스케줄링 지점 (락 해제 직전)      →  signal_pending_ipi() →  실제 IPI 발송
```

`flag_ipi()`는 원자 OR 한 번이 전부입니다([ipi.c:19-26](zephyr/kernel/smp/ipi.c#L19-L26)).

### 6.2 `signal_pending_ipi()` — 자기 비트를 남긴다

[ipi.c:72-105](zephyr/kernel/smp/ipi.c#L72-L105)

```c
uint32_t self_bit = BIT(_current_cpu->id);
cpu_bitmap = (uint32_t)atomic_and(&_kernel.pending_ipi, (atomic_val_t)self_bit);
cpu_bitmap &= ~self_bit;
if (cpu_bitmap != 0) {
	arch_sched_directed_ipi(cpu_bitmap);   /* 또는 broadcast */
}
```

`atomic_and(&pending_ipi, self_bit)`는 **자기 비트만 남기고 나머지를 지우면서** 이전 값을 반환합니다. 즉 다른 CPU들의 비트만 소비하고 **자기 비트는 보존**합니다.

이유가 주석에 명시되어 있습니다([ipi.c:74-78](zephyr/kernel/smp/ipi.c#L74-L78)):

> `arch_sched_directed_ipi()`는 호출한 CPU를 건너뛰므로, 여기서 자기 비트를 지우면 **자신에게 온 IPI를 소리 없이 잃어버립니다.** 비트를 남겨 두면 이 CPU의 다음 인터럽트 진입에서 그것을 보고 스케줄러를 돌립니다.

두 번째 주석은 **호출 위치에 대한 계약**입니다([ipi.c:80-85](zephyr/kernel/smp/ipi.c#L80-L85)):

> 재스케줄 시 `signal_pending_ipi()`는 **스케줄러 락을 쥔 채** 호출되어야 한다. 그래야 스케줄링 결정과 IPI 발송이 원자적이 된다 — 동시에 일어난 `flag_ipi()`는 락 획득 **전**에 반영되어 이 CPU가 새 스레드를 보게 되거나, 락 해제 **후**에 반영되어 다른 CPU가 IPI를 발송하게 된다.

실제로 `z_get_next_switch_handle()`은 이 호출을 스핀락 **안**에 두고, 그 이유를 다시 적어둡니다([sched.c:749-752](zephyr/kernel/sched.c#L749-L752)) — "다른 CPU가 우리를 위해 세운 재스케줄 IPI를 소리 없이 소비하지 않기 위해".

반면 `z_reschedule_irqlock()`에는 **알려진 위반**이 TODO로 남아 있습니다([sched.c:618-623](zephyr/kernel/sched.c#L618-L623)): IRQ 락만 쥐고 `_sched_spinlock`은 쥐지 않은 채 호출하므로 재스케줄이 지연될 수 있다고 인정합니다.

### 6.3 `ipi_mask_create()` — 누구에게 보낼 것인가

[ipi.c:29-70](zephyr/kernel/smp/ipi.c#L29-L70)

```c
if (!IS_ENABLED(CONFIG_IPI_OPTIMIZE)) {
	return (CONFIG_MP_MAX_NUM_CPUS > 1) ? IPI_ALL_CPUS_MASK : 0;
}
```

**기본값(`IPI_OPTIMIZE=n`)에서는 계산 없이 전체 마스크**입니다. 본 타깃도 여기에 해당합니다.

옵션을 켜면 CPU마다 다음 네 조건을 따집니다([ipi.c:46-54](zephyr/kernel/smp/ipi.c#L46-L54)).

| # | 조건 | 성격 |
|---|---|---|
| 1 | CPU가 비활성 | **절대 불필요** |
| 2 | 대상 CPU에서 스레드를 실행할 수 없음 (`cpu_mask`) | **절대 불필요** |
| 3 | 대상 CPU의 현재 스레드가 선점 불가 | 불필요할 **수도** 있음 |
| 4 | 대상 CPU의 현재 스레드 우선순위가 더 높음 | 불필요할 **수도** 있음 |

3·4는 **MetaIRQ 스레드면 무효**입니다 — MetaIRQ는 협조적 스레드도 선점하기 때문입니다(`02-스케줄러.md` §6).

트레이드오프가 Kconfig에 정리되어 있습니다([smp/Kconfig:127-143](zephyr/kernel/smp/Kconfig#L127-L143)): 스케줄러 연산마다 O(N) 계산이 추가되는 대신 인터럽트 수가 줄며, 어느 쪽이 이득인지는 **애플리케이션 거동에 달렸다**고 명시합니다.

### 6.4 아키텍처 구현 (ARM64)

[arm64/core/smp.c:214-229](zephyr/arch/arm64/core/smp.c#L214-L229)

```c
void sched_ipi_handler(const void *unused) { z_sched_ipi(); }
void arch_sched_broadcast_ipi(void)        { send_ipi(SGI_SCHED_IPI, IPI_ALL_CPUS_MASK); }
void arch_sched_directed_ipi(uint32_t cpu_bitmap) { send_ipi(SGI_SCHED_IPI, cpu_bitmap); }
```

GIC의 **SGI(Software Generated Interrupt) 0번**을 사용하며, `arch_smp_init()`에서 연결합니다([arm64/core/smp.c:295-305](zephyr/arch/arm64/core/smp.c#L295-L305)). 소스 주석은 SGI 번호를 나중에 Kconfig로 뺄 수 있다고 언급합니다.

ARM64는 IPI를 목적지별로 보낼 수 있어 `ARCH_HAS_DIRECTED_IPIS`를 select합니다. 다만 `IPI_OPTIMIZE`가 꺼져 있으면 마스크가 항상 전체이므로, **실질적으로는 "자신을 제외한 전원에게 directed 발송"**이 됩니다. 하드웨어 능력은 있으나 정책이 그것을 활용하지 않는 상태입니다. (확신도: high — 두 옵션의 값과 코드 경로에서 직접 도출)

### 6.5 수신 측 — `z_sched_ipi()`

[ipi.c:190-212](zephyr/kernel/smp/ipi.c#L190-L212)

```c
void z_sched_ipi(void)
{
#ifdef CONFIG_TIMESLICING
	z_time_slice();
#endif
#ifdef CONFIG_ARCH_IPI_LAZY_COPROCESSORS_SAVE
	arch_ipi_lazy_coprocessors_save();
#endif
#ifdef CONFIG_SCHED_IPI_SUPPORTED
	ipi_work_process(&_kernel.cpus[cpu_id].ipi_workq, cpu_id);
#endif
}
```

함수 자체는 **재스케줄을 직접 하지 않습니다.** ISR이므로 반환 시 아키텍처 코드가 `z_get_next_switch_handle()`을 부르고, 거기서 `next_up()`이 결정합니다. 즉 IPI의 역할은 "**대상 CPU를 인터럽트에 빠뜨려 스케줄링 지점을 만드는 것**"입니다. `03-스레드-생명주기.md` §7.2에서 halt 요청받은 CPU가 스스로 멈추는 것도 이 경로입니다.

맨 위 주석이 유지보수 함정을 경고합니다([ipi.c:192-194](zephyr/kernel/smp/ipi.c#L192-L194)) — 여기에 코드를 추가하면 `!CONFIG_SCHED_IPI_SUPPORTED` 환경에서도 적절한 위치에서 호출되도록 챙겨야 합니다.

### 6.6 `k_ipi_work` — 임의 함수를 원격 CPU에서 실행

[ipi.c:107-187](zephyr/kernel/smp/ipi.c#L107-L187), 구조체는 [kernel.h:3961-3972](zephyr/include/zephyr/kernel.h#L3961-L3972)

```c
struct k_ipi_work {
	sys_dnode_t    node[CONFIG_MP_MAX_NUM_CPUS];  /* CPU마다 별도 노드 */
	k_ipi_func_t   func;
	struct k_event event;                          /* 완료 통지 */
	uint32_t       bitmask;                        /* 대상 CPU */
};
```

**노드 배열이 CPU 개수만큼** 있는 것이 핵심입니다. 하나의 work 항목이 여러 CPU의 큐에 **동시에** 들어가야 하므로, 큐마다 별도 링크 노드가 필요합니다.

완료 통지는 `k_event`로 하며(`SCHED_IPI_SUPPORTED`가 `EVENTS`를 select, [smp/Kconfig:34](zephyr/kernel/smp/Kconfig#L34)), 각 CPU가 처리를 마치면 자기 비트를 post합니다([ipi.c:183](zephyr/kernel/smp/ipi.c#L183)). 호출자는 `k_ipi_work_wait()`로 전원 완료를 기다립니다.

재사용 방어와 자기 CPU 제외 처리도 있습니다([ipi.c:124-139](zephyr/kernel/smp/ipi.c#L124-L139)):

- 이미 사용 중인 work 항목이면 `-EBUSY`
- 대상 마스크에서 **현재 CPU를 제거** — "아키텍처가 자기 자신에게 IPI를 보내는 것을 지원하지 않을 수 있으므로"

`ipi_work_process()`는 콜백 호출 전에 락을 놓고 이후 다시 잡습니다([ipi.c:171-187](zephyr/kernel/smp/ipi.c#L171-L187)). 타임아웃 announce 루프와 같은 패턴입니다(`04-타임아웃과-시스템-클럭.md` §5.2).

---

## 7. CPU 친화도

[cpu_mask.c](zephyr/kernel/smp/cpu_mask.c)

모든 API가 `cpu_mask_mod()` 하나로 수렴하며, **핵심 제약이 하나** 있습니다.

```c
K_SPINLOCK(&_sched_spinlock) {
	if (z_is_thread_prevented_from_running(thread)) {
		thread->base.cpu_mask |= enable_mask;
		thread->base.cpu_mask &= ~disable_mask;
	} else {
		ret = -EINVAL;
	}
}
```
[cpu_mask.c:28-43](zephyr/kernel/smp/cpu_mask.c#L28-L43)

**실행 가능한 상태의 스레드는 친화도를 바꿀 수 없습니다**(`-EINVAL`). 레디 큐에 들어 있거나 실행 중인 스레드의 마스크를 바꾸면 큐 배치와 실제 실행 CPU가 어긋나기 때문입니다. 따라서 친화도는 보통 **생성 직후, 시작 전**에 설정합니다.

`CONFIG_SCHED_CPU_MASK_PIN_ONLY`에서는 더 엄격합니다 — 마스크에 CPU가 **정확히 하나**여야 하며, 어서션이 비트 개수를 검사합니다([cpu_mask.c:33-38](zephyr/kernel/smp/cpu_mask.c#L33-L38)). 이 모드에서 레디 큐가 CPU별로 분리되기 때문입니다(`01-커널-코어-자료구조.md` §2).

백엔드별 마스크 검색 비용은 Kconfig에 상세히 정리되어 있습니다([smp/Kconfig:63-91](zephyr/kernel/smp/Kconfig#L63-L91)) — SIMPLE은 O(N)이되 우선순위 정렬 덕에 조기 종료, MULTIQ는 O(P) 비트맵 순회 후 레벨 내 스캔.

---

## 8. 보조 CPU 부팅

[smp.c:101-240](zephyr/kernel/smp/smp.c#L101-L240)

### 8.1 두 단계 핸드셰이크

플래그 두 개가 **서로 반대 방향**으로 동작합니다.

| 플래그 | 방향 | 의미 |
|---|---|---|
| `ready_flag` | 신규 CPU → 부팅 요청자 | "전원이 들어와 초기화 준비가 됐다" |
| `cpu_start_flag` | 부팅 요청자 → 신규 CPU | "이제 초기화를 진행해도 좋다" |

```
start_cpu(id):                      smp_init_top() [신규 CPU]:
  ready_flag = 0
  arch_cpu_start(...)  ─────────▶     ready_flag = 1
  while (!ready_flag)                 wait_for_start_signal(&cpu_start_flag)
      local_delay()                       ↑ 여기서 대기
  return                                  │
                                          │
z_smp_init(): 모든 CPU 기동 후            │
  cpu_start_flag = 1  ──────────────────▶ 진행
                                        z_dummy_thread_init()
                                        smp_timer_init()
                                        csc.fn()
                                        z_swap_unlocked()  → 스케줄러 진입
```

`z_smp_init()`은 CPU를 하나씩 깨우되 `cpu_start_flag`를 **마지막에** 세웁니다([smp.c:221-240](zephyr/kernel/smp/smp.c#L221-L240)). 모든 CPU가 **동시에** 스케줄러에 진입하도록 동기화하기 위해서입니다.

반면 CPU 하나만 켜는 `k_smp_cpu_start()`/`k_smp_cpu_resume()`은 동기화가 필요 없으므로 플래그를 곧바로 1로 둡니다([smp.c:181-184](zephyr/kernel/smp/smp.c#L181-L184)).

### 8.2 `local_delay()`

```c
static inline void local_delay(void)
{
	for (volatile int i = 0; i < 1000; i++) { }
}
```
[smp.c:92-99](zephyr/kernel/smp/smp.c#L92-L99)

원자 변수를 폴링할 때 **공유 버스를 도배하지 않으려는** 지연입니다. 아직 커널 서비스가 없는 시점이라 타이머를 쓸 수 없어 빈 루프를 씁니다.

### 8.3 `smp_init_top()`의 분기

[smp.c:109-148](zephyr/kernel/smp/smp.c#L109-L148)

인자 `arg`가 `NULL`이면(= `z_smp_init()`의 일반 부팅) 더미 스레드 초기화, 타이머 재초기화, 스케줄러 진입을 **모두** 수행합니다. `k_smp_cpu_resume()`으로 온 경우에는 `invoke_sched`/`reinit_timer` 플래그에 따라 선택적으로 수행하고, 스케줄러에 진입하지 않고 **반환할 수도** 있습니다.

더미 스레드가 필요한 이유는 `03-스레드-생명주기.md` §10과 같습니다 — 첫 스레드로 스위치하려면 "빠져나올 컨텍스트"가 있어야 하고, 그 저장 상태는 버려집니다.

### 8.4 CPU별 상태 초기화

[init.c:395-429](zephyr/kernel/init.c#L395-L429)

`z_init_cpu()`가 아이들 스레드 생성·연결, `id`, 인터럽트 스택 포인터, IPI 워크 큐를 설정합니다. **CPU마다 아이들 스레드가 하나씩** 있다는 점(`01-커널-코어-자료구조.md` §3)이 여기서 확정됩니다.

---

## 9. `arch_spin_relax()` — 스핀 중 데드락 회피

스핀 루프에서 호출되는 이 훅은 겉보기에는 단순한 "잠깐 쉬기"이지만, 아키텍처에 따라 **데드락 회피 장치**가 됩니다.

### 기본 구현

```c
void __weak arch_spin_relax(void)
{
	__ASSERT(!arch_cpu_irqs_are_enabled(),
		 "this is meant to be called with IRQs disabled");
	arch_nop();
}
```
[idle.c:97-104](zephyr/kernel/idle.c#L97-L104)

`__weak`이므로 아키텍처가 덮어쓸 수 있습니다. 어서션은 **인터럽트가 차단된 상태에서만 호출되어야 함**을 못박습니다 — §2.2의 순서(IRQ 먼저, 스핀 나중)가 이를 보장합니다.

### ARM64의 재정의 — FPU 데드락

[arm64/core/smp.c:275-292](zephyr/arch/arm64/core/smp.c#L275-L292)

```c
void arch_spin_relax(void)
{
	if (arm_gic_irq_is_pending(SGI_FPU_IPI)) {
		arm_gic_irq_clear_pending(SGI_FPU_IPI);
		arch_float_disable(_current_cpu->arch.fpu_owner);
	}
}
```

주석이 시나리오를 설명합니다:

> 경합 중인 스핀락을 기다리는 동안 이 CPU에 대한 **FPU 플러시 요청이 밀려 있지 않도록** 한다. 그러지 않으면, 우리가 필요한 락을 이미 쥔 다른 CPU가 **자기 FPU 내용 복원을 기다리는데 그 내용이 아직 이 CPU의 FPU에 살아 있는** 데드락이 발생한다.

인터럽트가 차단된 상태이므로 SGI가 **전달되지 못하고 pending에만 남습니다.** 그래서 스핀 루프가 직접 pending 비트를 확인해 처리합니다. 인터럽트를 못 받는 상황에서 인터럽트가 할 일을 대신 수행하는 구조입니다.

> **본 타깃에서는 이 코드가 컴파일되지 않습니다.** `#ifdef CONFIG_FPU_SHARING` 안에 있고 `FPU`/`FPU_SHARING` 모두 기본 n이므로, weak 기본 구현(`arch_nop()`)이 쓰입니다. (확신도: high — `arch/Kconfig:1210-1239`에 default 선언 없음)

---

## 10. 정리

- **스핀락은 "인터럽트 차단 + 원자 변수"** 그 이상이 아니며, 전부 헤더 인라인입니다. UP에서는 인터럽트 마스킹으로 축약되고 구조체는 비어버려, 언어 간 ABI를 위한 더미 멤버까지 필요해집니다.
- **획득 순서(IRQ → 스핀)가 계약을 만듭니다.** 반대로 하면 ISR 재진입 데드락이 생기고, `arch_spin_relax()`의 어서션도 이 순서를 전제합니다.
- **검증 프레임워크는 포인터와 카운트를 함께 추적**합니다. 하나만으로는 `lock(A); lock(B); unlock(A); swap()` 같은 어긋난 중첩을 잡지 못합니다.
- **IPI는 모았다가 스케줄링 지점에서 한 번에 보냅니다.** `signal_pending_ipi()`가 자기 비트를 남기는 것, 그리고 스케줄러 락 아래에서 호출해야 하는 것 — 두 규칙이 IPI 유실과 결정/발송의 비원자성을 각각 막습니다.
- **IPI는 재스케줄을 직접 하지 않습니다.** 대상 CPU를 인터럽트에 빠뜨려 스케줄링 지점을 만들 뿐이고, 결정은 인터럽트 복귀 시 `next_up()`이 합니다.
- **하드웨어 능력과 정책은 별개입니다.** ARM64는 directed IPI를 지원하지만 `IPI_OPTIMIZE`가 꺼져 있어 마스크는 항상 전체입니다.

## 11. 다음 문서

- `z_pend_curr()` / `z_sched_wake()` 위에 쌓이는 IPC 객체들: `06-동기화-객체.md` (예정)
