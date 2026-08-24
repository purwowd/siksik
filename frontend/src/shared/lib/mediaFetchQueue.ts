/** Limit parallel media blob fetches (findings table can request many at once). */

const MAX_CONCURRENT = 3;
let active = 0;
const waiters: Array<() => void> = [];

function pump() {
  while (active < MAX_CONCURRENT && waiters.length > 0) {
    const next = waiters.shift();
    if (next) next();
  }
}

export function enqueueMediaTask<T>(task: () => Promise<T>): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const start = () => {
      active += 1;
      task()
        .then(resolve, reject)
        .finally(() => {
          active -= 1;
          pump();
        });
    };
    if (active < MAX_CONCURRENT) start();
    else waiters.push(start);
  });
}
