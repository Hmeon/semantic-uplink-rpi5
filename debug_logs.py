import pandas as pd
from pathlib import Path
import sys

# Force utf-8 output
sys.stdout.reconfigure(encoding='utf-8')

log_dir = Path(r'artifacts/slow10_linucb_3h_aiot/logs')
event_files = sorted(list(log_dir.glob('events_*.parquet')))

with open('debug_output.txt', 'w', encoding='utf-8') as f:
    if not event_files:
        f.write('No event files found.\n')
    else:
        f.write(f'Found {len(event_files)} event files.\n')
        try:
            df = pd.concat([pd.read_parquet(p) for p in event_files], ignore_index=True)
            f.write(f'Total Events: {len(df)}\n')
            if len(df) > 0:
                df['ts'] = pd.to_datetime(df['ts'])
                duration = (df['ts'].max() - df['ts'].min()).total_seconds()
                f.write(f'Duration: {duration:.2f} s\n')
                
                if 'kbits' in df.columns:
                    f.write(f'Mean kbits: {df["kbits"].mean():.2f}\n')
                    # Rate estimation: ~30 bytes/msg (very rough)
                    total_bytes = len(df) * 30
                    f.write(f'Approx Rate: {total_bytes / duration:.2f} B/s\n')

                if 'aoi_ms' in df.columns:
                    f.write(f'Mean AoI: {df["aoi_ms"].mean():.2f}\n')
                    f.write(f'P95 AoI: {df["aoi_ms"].quantile(0.95):.2f}\n')
                if 'res' in df.columns:
                    f.write(f'Mean MAE: {df["res"].abs().mean():.2f}\n')
        except Exception as e:
            f.write(f'Error: {e}\n')
