import json
import re
import pandas as pd
import matplotlib.pyplot as plt
from datetime import datetime, timezone, timedelta
from pathlib import Path
import glob
import sys
import argparse

def extract_results_from_js(js_file_path):
    """Extract the results object from results.js"""
    try:
        with open(js_file_path, 'r') as f:
            content = f.read()
        
        match = re.search(r'(?:let|const|var)\s+results\s*=\s*({.*?});', content, re.DOTALL)
        if match:
            return json.loads(match.group(1))
        
        match = re.search(r'(?:let|const|var)\s+results\s*=\s*({.*?})\s*$', content, re.DOTALL | re.MULTILINE)
        if match:
            return json.loads(match.group(1))
            
        return None
    except Exception as e:
        print(f"✗ Error parsing results.js: {e}")
        return None

def process_results_js(results):
    """Extract PSS data from Process Monitor results"""
    pss_data = []
    
    for process in results.get('processes', []):
        if 'pss' in process and 'start' in process:
            pss_data.append({
                'timestamp': datetime.fromtimestamp(process['start'] / 1000, tz=timezone.utc),
                'pss_mb': process['pss'] / 1024,  # Convert KB to MB
                'pid': process['pid'],
                'name': process.get('content', 'Unknown')
            })
    
    return pd.DataFrame(pss_data)

def parse_meminsight_filename(filename):
    """Parse meminsight filename to extract timestamp"""
    match = re.search(r'_(\d{14})_iter(\d+)_', filename)
    if match:
        timestamp_str = match.group(1)
        iter_num = int(match.group(2))
        timestamp = datetime.strptime(timestamp_str, '%Y%m%d%H%M%S').replace(tzinfo=timezone.utc)
        return timestamp, iter_num
    return None, None

def parse_meminsight_csv(csv_file):
    """Parse a single meminsight CSV file and extract PSS values"""
    try:
        with open(csv_file, 'r') as f:
            lines = f.readlines()
        
        # Find the line that starts with "PID,EXE,RSS,PSS"
        header_index = None
        for i, line in enumerate(lines):
            if line.strip().startswith('PID,EXE,RSS,PSS'):
                header_index = i
                break
        
        if header_index is None:
            return None, None, "No process header found"
        
        # Parse process data
        pss_values = []
        for i in range(header_index + 1, len(lines)):
            line = lines[i].strip()
            if not line or line.startswith('#'):
                continue
            
            parts = line.split(',')
            if len(parts) < 4:
                continue
            
            try:
                pid = parts[0].strip()
                pss = float(parts[3].strip())
                
                # Skip the Total line (PID = 0)
                if pid == '0' or pid.lower() == 'total':
                    continue
                
                pss_values.append(pss)
            except (ValueError, IndexError):
                continue
        
        if not pss_values:
            return None, None, "No valid PSS data found"
        
        # Calculate total PSS and average PSS per process
        total_pss = sum(pss_values)
        average_pss = total_pss / len(pss_values) if pss_values else 0
        return total_pss, average_pss, None
        
    except Exception as e:
        return None, None, str(e)

def process_meminsight_files(meminsight_dir, pm_start=None, pm_end=None, time_offset_hours=0, auto_detect=True):
    """Process all meminsight CSV files with smart time alignment"""
    csv_files = sorted(glob.glob(str(Path(meminsight_dir) / '*_meminsight.csv')))
    
    if not csv_files:
        print(f"   ⚠ Warning: No CSV files found in {meminsight_dir}")
        return pd.DataFrame()
    
    print(f"   Found {len(csv_files)} CSV files")
    
    all_data = []
    skipped = []
    
    # First pass: collect all timestamps to detect grouping
    file_info = []
    for csv_file in csv_files:
        filename = Path(csv_file).name
        timestamp, iter_num = parse_meminsight_filename(filename)
        if timestamp:
            file_info.append((csv_file, filename, timestamp, iter_num))
    
    # Auto-detect timezone offset if enabled and PM range is available
    if auto_detect and pm_start and pm_end and file_info:
        print(f"\n   🔍 Auto-detecting time alignment...")
        
        # Group files by hour to detect different capture sessions
        hour_groups = {}
        for _, fname, ts, iter_num in file_info:
            hour_key = ts.replace(minute=0, second=0, microsecond=0)
            if hour_key not in hour_groups:
                hour_groups[hour_key] = []
            hour_groups[hour_key].append((fname, ts, iter_num))
        
        print(f"      Found {len(hour_groups)} time group(s):")
        for hour_key, files in sorted(hour_groups.items()):
            iter_range = f"iter {min(f[2] for f in files)}-{max(f[2] for f in files)}"
            print(f"         {hour_key}: {len(files)} files ({iter_range})")
    
    # Process each file
    for csv_file, filename, timestamp, iter_num in file_info:
        original_timestamp = timestamp
        
        # Try with and without offset to see which fits better
        best_timestamp = timestamp
        best_offset = 0
        
        if auto_detect and pm_start and pm_end:
            # Try different offsets
            for offset in [0, 5, -5]:
                test_ts = timestamp + timedelta(hours=offset)
                if pm_start <= test_ts <= pm_end:
                    best_timestamp = test_ts
                    best_offset = offset
                    break
        else:
            # Apply manual offset
            if time_offset_hours != 0:
                best_timestamp = timestamp + timedelta(hours=time_offset_hours)
                best_offset = time_offset_hours
        
        # Check if within range
        if pm_start and pm_end:
            if best_timestamp < pm_start or best_timestamp > pm_end:
                skipped.append((filename, original_timestamp, best_timestamp, best_offset, "Outside PM time range"))
                continue
        
        total_pss, average_pss, error = parse_meminsight_csv(csv_file)
        
        if error:
            skipped.append((filename, original_timestamp, best_timestamp, best_offset, error))
            continue
        
        if total_pss is not None and average_pss is not None:
            all_data.append({
                'timestamp': best_timestamp,
                'pss_mb': total_pss / 1024,  # Convert KB to MB
                'average_pss_mb': average_pss / 1024,  # Convert KB to MB
                'num_processes': int(total_pss / average_pss) if average_pss > 0 else 0,
                'iter': iter_num,
                'file': filename,
                'offset_applied': best_offset
            })
    
    if skipped:
        print(f"\n   ⚠ Skipped {len(skipped)} file(s):")
        for fname, orig_ts, adj_ts, offset, reason in skipped[:5]:
            if offset != 0:
                print(f"      - {fname}")
                print(f"        Original: {orig_ts}, Adjusted (+{offset}h): {adj_ts}")
                print(f"        Reason: {reason}")
            else:
                print(f"      - {fname} ({orig_ts}): {reason}")
        if len(skipped) > 5:
            print(f"      ... and {len(skipped) - 5} more")
    
    if all_data:
        result_df = pd.DataFrame(all_data)
        result_df = result_df.sort_values('timestamp')
        print(f"\n   ✓ Successfully processed {len(result_df)} files")
        print(f"   Iteration range: {result_df['iter'].min()} to {result_df['iter'].max()}")
        
        # Show which offsets were applied
        offset_counts = result_df['offset_applied'].value_counts().sort_index()
        print(f"\n   Offset adjustments applied:")
        for offset, count in offset_counts.items():
            print(f"      {offset:+d} hours: {count} files")
        
        print(f"\n   Sample data:")
        for _, row in result_df.head(3).iterrows():
            offset_str = f" (offset: {row['offset_applied']:+d}h)" if row['offset_applied'] != 0 else ""
            print(f"      Iter {row['iter']:2d}: {row['timestamp']} - Total PSS: {row['pss_mb']:>10,.1f} MB ({row['num_processes']} procs){offset_str}")
        if len(result_df) > 3:
            print(f"      ...")
            for _, row in result_df.tail(2).iterrows():
                offset_str = f" (offset: {row['offset_applied']:+d}h)" if row['offset_applied'] != 0 else ""
                print(f"      Iter {row['iter']:2d}: {row['timestamp']} - Total PSS: {row['pss_mb']:>10,.1f} MB ({row['num_processes']} procs){offset_str}")
        
        return result_df
    else:
        print("   ✗ Error: No data could be extracted from CSV files")
        return pd.DataFrame()

def plot_pss_comparison(process_monitor_df, meminsight_df, output_path=None):
    """Plot PSS data from both sources"""
    fig, ax = plt.subplots(figsize=(16, 8))
    
    has_pm_data = False
    has_mi_data = False
    
    # Plot Process Monitor data (aggregated by second)
    if not process_monitor_df.empty:
        pm_grouped = process_monitor_df.groupby(
            process_monitor_df['timestamp'].dt.floor('S')
        )['pss_mb'].sum().reset_index()
        
        ax.plot(pm_grouped['timestamp'], pm_grouped['pss_mb'], 
                color='red', linewidth=2, label='Process Monitor (Total PSS)', 
                marker='o', markersize=4, alpha=0.8)
        print(f"   ✓ Plotted {len(pm_grouped)} Process Monitor data points")
        has_pm_data = True
    
    # Plot meminsight data
    if not meminsight_df.empty:
        ax.plot(meminsight_df['timestamp'], meminsight_df['pss_mb'], 
                color='green', linewidth=2, label='Meminsight (Total PSS)', 
                marker='s', markersize=5, alpha=0.8)
        print(f"   ✓ Plotted {len(meminsight_df)} Meminsight data points")
        has_mi_data = True
    
    if not has_pm_data and not has_mi_data:
        print("   ✗ No data to plot")
        plt.close(fig)
        return
    
    ax.set_xlabel('Timestamp (UTC)', fontsize=12, fontweight='bold')
    ax.set_ylabel('Total PSS (MB)', fontsize=12, fontweight='bold')
    ax.set_title('PSS Comparison: Process Monitor vs Meminsight', 
                 fontsize=14, fontweight='bold')
    ax.legend(loc='best', fontsize=11)
    ax.grid(True, alpha=0.3, linestyle='--')
    
    # Format y-axis with commas
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, p: f'{int(x):,}'))
    
    # Format x-axis to show time nicely
    plt.xticks(rotation=45, ha='right')
    fig.tight_layout()
    
    if output_path is None:
        output_path = Path.cwd() / 'pss_comparison.png'
    else:
        output_path = Path(output_path)
    
    plt.savefig(output_path, dpi=300, bbox_inches='tight')
    print(f"\n✓ Plot saved to: {output_path}")
    
    plt.show()

def print_statistics(pm_df, mi_df):
    """Print statistics summary"""
    print("\n" + "=" * 70)
    print("STATISTICS SUMMARY")
    print("=" * 70)
    
    if not pm_df.empty:
        pm_grouped = pm_df.groupby(pm_df['timestamp'].dt.floor('S'))['pss_mb'].sum()
        print(f"\n📊 Process Monitor:")
        print(f"   Time Range:      {pm_df['timestamp'].min()} to {pm_df['timestamp'].max()}")
        print(f"   Data points:     {len(pm_grouped)}")
        print(f"   Min PSS:         {pm_grouped.min():>15,.1f} MB")
        print(f"   Max PSS:         {pm_grouped.max():>15,.1f} MB")
        print(f"   Mean PSS:        {pm_grouped.mean():>15,.1f} MB")
        print(f"   Median PSS:      {pm_grouped.median():>15,.1f} MB")
        print(f"   Std Dev:         {pm_grouped.std():>15,.1f} MB")
    else:
        print(f"\n📊 Process Monitor: No data")
    
    if not mi_df.empty:
        print(f"\n📊 Meminsight:")
        print(f"   Time Range:      {mi_df['timestamp'].min()} to {mi_df['timestamp'].max()}")
        print(f"   Data points:     {len(mi_df)}")
        print(f"   Iterations:      {mi_df['iter'].min()} to {mi_df['iter'].max()}")
        print(f"   Avg # Processes: {mi_df['num_processes'].mean():.0f}")
        print(f"   Min Total PSS:   {mi_df['pss_mb'].min():>15,.1f} MB")
        print(f"   Max Total PSS:   {mi_df['pss_mb'].max():>15,.1f} MB")
        print(f"   Mean Total PSS:  {mi_df['pss_mb'].mean():>15,.1f} MB")
        print(f"   Median Total PSS:{mi_df['pss_mb'].median():>15,.1f} MB")
        print(f"   Std Dev:         {mi_df['pss_mb'].std():>15,.1f} MB")
        
        if len(mi_df) > 1:
            pss_change = mi_df['pss_mb'].iloc[-1] - mi_df['pss_mb'].iloc[0]
            pss_change_pct = (pss_change / mi_df['pss_mb'].iloc[0]) * 100 if mi_df['pss_mb'].iloc[0] > 0 else 0
            print(f"   Total PSS Change:{pss_change:>15,.1f} MB ({pss_change_pct:+.2f}%)")
        
        # Show average PSS per process
        print(f"\n   Average PSS per Process (for reference):")
        print(f"   Min Avg PSS:     {mi_df['average_pss_mb'].min():>15,.1f} MB")
        print(f"   Max Avg PSS:     {mi_df['average_pss_mb'].max():>15,.1f} MB")
        print(f"   Mean Avg PSS:    {mi_df['average_pss_mb'].mean():>15,.1f} MB")
    else:
        print(f"\n📊 Meminsight: No data")
    
    # Comparison
    if not pm_df.empty and not mi_df.empty:
        pm_mean = pm_df.groupby(pm_df['timestamp'].dt.floor('S'))['pss_mb'].sum().mean()
        mi_mean_total = mi_df['pss_mb'].mean()
        mi_mean_avg = mi_df['average_pss_mb'].mean()
        
        print(f"\n📈 Comparison:")
        print(f"   Process Monitor Mean (Total):      {pm_mean:>15,.1f} MB")
        print(f"   Meminsight Mean (Total):           {mi_mean_total:>15,.1f} MB")
        print(f"   Meminsight Mean (Avg per Process): {mi_mean_avg:>15,.1f} MB")
        
        diff = abs(pm_mean - mi_mean_total)
        diff_pct = (diff / max(pm_mean, mi_mean_total)) * 100 if max(pm_mean, mi_mean_total) > 0 else 0
        print(f"   Difference (Total PSS):            {diff:>15,.1f} MB ({diff_pct:.2f}%)")
        
        if pm_mean > mi_mean_total:
            print(f"   Process Monitor captures {((pm_mean/mi_mean_total - 1) * 100):.2f}% more PSS on average")
        else:
            print(f"   Meminsight captures {((mi_mean_total/pm_mean - 1) * 100):.2f}% more PSS on average")
    
    print("\n" + "=" * 70)

def main():
    parser = argparse.ArgumentParser(
        description='Analyze and compare PSS data from Process Monitor and Meminsight',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Auto-detect time alignment (recommended)
  python analyze_pss_offset.py --js results.js --csv meminsight/
  
  # Disable auto-detection and use manual offset
  python analyze_pss_offset.py --js results.js --csv meminsight/ --offset 5 --no-auto
  
  # With custom output
  python analyze_pss_offset.py --js results.js --csv meminsight/ -o plot.png
        """
    )
    
    parser.add_argument('--js', type=str, required=True, help='Path to results.js file')
    parser.add_argument('--csv', type=str, required=True, help='Path to meminsight CSV directory')
    parser.add_argument('--output', '-o', type=str, default=None, help='Output plot path')
    parser.add_argument('--offset', type=int, default=0, 
                       help='Manual time offset in hours (use with --no-auto)')
    parser.add_argument('--no-auto', action='store_true',
                       help='Disable auto-detection of time alignment')
    
    args = parser.parse_args()
    
    js_path = Path(args.js)
    csv_dir = Path(args.csv)
    
    print("=" * 70)
    print("PSS ANALYSIS: Process Monitor vs Meminsight")
    print("=" * 70)
    
    # Validate paths
    print("\n1. Validating paths...")
    if not js_path.exists() or not js_path.is_file():
        print(f"   ✗ Error: results.js not found: {js_path}")
        sys.exit(1)
    print(f"   ✓ Found results.js: {js_path.absolute()}")
    
    if not csv_dir.exists() or not csv_dir.is_dir():
        print(f"   ✗ Error: CSV directory not found: {csv_dir}")
        sys.exit(1)
    print(f"   ✓ Found CSV directory: {csv_dir.absolute()}")
    
    # Process results.js
    print("\n2. Processing Process Monitor data...")
    results = extract_results_from_js(js_path)
    if results:
        pm_df = process_results_js(results)
        if not pm_df.empty:
            pm_start = pm_df['timestamp'].min()
            pm_end = pm_df['timestamp'].max()
            print(f"   ✓ Extracted {len(pm_df)} process records")
            print(f"   Time range: {pm_start} to {pm_end}")
            total_pss = pm_df.groupby(pm_df['timestamp'].dt.floor('S'))['pss_mb'].sum().sum()
            print(f"   Total PSS: {total_pss:,.1f} MB")
        else:
            print("   ⚠ Warning: No PSS data in results.js")
            pm_start = pm_end = None
    else:
        print("   ✗ Error: Could not parse results.js")
        pm_df = pd.DataFrame()
        pm_start = pm_end = None
    
    # Process meminsight files
    auto_mode = "disabled" if args.no_auto else "enabled"
    print(f"\n3. Processing Meminsight CSV files (auto-align: {auto_mode})...")
    mi_df = process_meminsight_files(csv_dir, pm_start, pm_end, args.offset, not args.no_auto)
    
    # Generate plot
    print("\n4. Generating comparison plot...")
    if not pm_df.empty or not mi_df.empty:
        output_path = args.output if args.output else 'pss_comparison.png'
        plot_pss_comparison(pm_df, mi_df, output_path)
        print_statistics(pm_df, mi_df)
    else:
        print("   ✗ Error: No data available")
        sys.exit(1)

if __name__ == '__main__':
    main()