import json
import re
import pandas as pd
import matplotlib.pyplot as plt
from datetime import datetime
from pathlib import Path
import glob
import sys
import argparse

def extract_results_from_js(js_file_path):
    """Extract the results object from results.js"""
    try:
        with open(js_file_path, 'r') as f:
            content = f.read()
        
        # Extract JSON from "let results = {...};" or "const results = {...};" or "var results = {...};"
        match = re.search(r'(?:let|const|var)\s+results\s*=\s*({.*?});', content, re.DOTALL)
        if match:
            return json.loads(match.group(1))
        
        # Try without semicolon
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
                'timestamp': datetime.fromtimestamp(process['start'] / 1000),
                'pss_kb': process['pss'],
                'pid': process['pid'],
                'name': process.get('content', 'Unknown')
            })
    
    return pd.DataFrame(pss_data)

def parse_meminsight_filename(filename):
    """Parse meminsight filename to extract timestamp"""
    # Format: f0463b5c0008_20251117062026_iter1_meminsight.csv
    match = re.search(r'_(\d{14})_iter(\d+)_', filename)
    if match:
        timestamp_str = match.group(1)
        iter_num = int(match.group(2))
        timestamp = datetime.strptime(timestamp_str, '%Y%m%d%H%M%S')
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
            return None, "No process header found"
        
        # Parse process data
        pss_values = []
        for i in range(header_index + 1, len(lines)):
            line = lines[i].strip()
            if not line or line.startswith('#'):
                continue
            
            parts = line.split(',')
            if len(parts) < 4:
                continue
            
            # Get the 4th column (index 3) which is PSS
            try:
                pid = parts[0].strip()
                pss = float(parts[3].strip())
                
                # Skip the Total line (PID = 0) and lines with PID = 0
                if pid == '0' or pid.lower() == 'total':
                    continue
                
                pss_values.append(pss)
            except (ValueError, IndexError):
                continue
        
        if not pss_values:
            return None, "No valid PSS data found"
        
        # Calculate total PSS (sum of all processes)
        total_pss = sum(pss_values)
        return total_pss, None
        
    except Exception as e:
        return None, str(e)

def process_meminsight_files(meminsight_dir):
    """Process all meminsight CSV files and aggregate PSS data"""
    csv_files = sorted(glob.glob(str(Path(meminsight_dir) / '*_meminsight.csv')))
    
    if not csv_files:
        print(f"   ⚠ Warning: No CSV files found in {meminsight_dir}")
        return pd.DataFrame()
    
    print(f"   Found {len(csv_files)} CSV files")
    
    all_data = []
    errors = []
    
    for csv_file in csv_files:
        filename = Path(csv_file).name
        timestamp, iter_num = parse_meminsight_filename(filename)
        
        if timestamp is None:
            print(f"   ⚠ Warning: Could not parse timestamp from {filename}")
            continue
        
        total_pss, error = parse_meminsight_csv(csv_file)
        
        if error:
            errors.append(f"{filename}: {error}")
            continue
        
        if total_pss is not None:
            all_data.append({
                'timestamp': timestamp,
                'pss_kb': total_pss,
                'iter': iter_num,
                'file': filename
            })
    
    if errors:
        print(f"   ⚠ Encountered {len(errors)} error(s):")
        for error in errors[:5]:  # Show first 5 errors
            print(f"      - {error}")
        if len(errors) > 5:
            print(f"      ... and {len(errors) - 5} more")
    
    if all_data:
        result_df = pd.DataFrame(all_data)
        result_df = result_df.sort_values('timestamp')  # Sort by timestamp
        print(f"   ✓ Successfully processed {len(result_df)} files")
        print(f"   Iteration range: {result_df['iter'].min()} to {result_df['iter'].max()}")
        
        # Show sample of data
        print(f"\n   Sample data:")
        for _, row in result_df.head(3).iterrows():
            print(f"      Iter {row['iter']:2d}: {row['timestamp']} - PSS: {row['pss_kb']:>10,.0f} KB")
        if len(result_df) > 3:
            print(f"      ...")
            for _, row in result_df.tail(2).iterrows():
                print(f"      Iter {row['iter']:2d}: {row['timestamp']} - PSS: {row['pss_kb']:>10,.0f} KB")
        
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
        # Aggregate PSS by second
        pm_grouped = process_monitor_df.groupby(
            process_monitor_df['timestamp'].dt.floor('S')
        )['pss_kb'].sum().reset_index()
        
        ax.plot(pm_grouped['timestamp'], pm_grouped['pss_kb'], 
                color='red', linewidth=2, label='Process Monitor (Total PSS)', 
                marker='o', markersize=4, alpha=0.8)
        print(f"   ✓ Plotted {len(pm_grouped)} Process Monitor data points")
        has_pm_data = True
    
    # Plot meminsight data
    if not meminsight_df.empty:
        ax.plot(meminsight_df['timestamp'], meminsight_df['pss_kb'], 
                color='green', linewidth=2, label='Meminsight (Total PSS)', 
                marker='s', markersize=5, alpha=0.8)
        print(f"   ✓ Plotted {len(meminsight_df)} Meminsight data points")
        has_mi_data = True
    
    if not has_pm_data and not has_mi_data:
        print("   ✗ No data to plot")
        plt.close(fig)
        return
    
    ax.set_xlabel('Timestamp', fontsize=12, fontweight='bold')
    ax.set_ylabel('Total PSS (KB)', fontsize=12, fontweight='bold')
    ax.set_title('PSS Comparison: Process Monitor vs Meminsight', 
                 fontsize=14, fontweight='bold')
    ax.legend(loc='best', fontsize=11)
    ax.grid(True, alpha=0.3, linestyle='--')
    
    # Format y-axis with commas
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, p: f'{int(x):,}'))
    
    # Format x-axis to show time nicely
    plt.xticks(rotation=45, ha='right')
    fig.tight_layout()
    
    # Save plot
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
        pm_grouped = pm_df.groupby(pm_df['timestamp'].dt.floor('S'))['pss_kb'].sum()
        print(f"\n📊 Process Monitor:")
        print(f"   Data points:     {len(pm_grouped)}")
        print(f"   Min PSS:         {pm_grouped.min():>15,.0f} KB")
        print(f"   Max PSS:         {pm_grouped.max():>15,.0f} KB")
        print(f"   Mean PSS:        {pm_grouped.mean():>15,.0f} KB")
        print(f"   Median PSS:      {pm_grouped.median():>15,.0f} KB")
        print(f"   Std Dev:         {pm_grouped.std():>15,.0f} KB")
    else:
        print(f"\n📊 Process Monitor: No data")
    
    if not mi_df.empty:
        print(f"\n📊 Meminsight:")
        print(f"   Data points:     {len(mi_df)}")
        print(f"   Iterations:      {mi_df['iter'].min()} to {mi_df['iter'].max()}")
        print(f"   Min PSS:         {mi_df['pss_kb'].min():>15,.0f} KB")
        print(f"   Max PSS:         {mi_df['pss_kb'].max():>15,.0f} KB")
        print(f"   Mean PSS:        {mi_df['pss_kb'].mean():>15,.0f} KB")
        print(f"   Median PSS:      {mi_df['pss_kb'].median():>15,.0f} KB")
        print(f"   Std Dev:         {mi_df['pss_kb'].std():>15,.0f} KB")
        
        # Calculate PSS change over time
        if len(mi_df) > 1:
            pss_change = mi_df['pss_kb'].iloc[-1] - mi_df['pss_kb'].iloc[0]
            pss_change_pct = (pss_change / mi_df['pss_kb'].iloc[0]) * 100 if mi_df['pss_kb'].iloc[0] > 0 else 0
            print(f"   PSS Change:      {pss_change:>15,.0f} KB ({pss_change_pct:+.2f}%)")
    else:
        print(f"\n📊 Meminsight: No data")
    
    # Comparison
    if not pm_df.empty and not mi_df.empty:
        pm_mean = pm_df.groupby(pm_df['timestamp'].dt.floor('S'))['pss_kb'].sum().mean()
        mi_mean = mi_df['pss_kb'].mean()
        diff = abs(pm_mean - mi_mean)
        diff_pct = (diff / max(pm_mean, mi_mean)) * 100 if max(pm_mean, mi_mean) > 0 else 0
        
        print(f"\n📈 Comparison:")
        print(f"   Process Monitor Mean: {pm_mean:>15,.0f} KB")
        print(f"   Meminsight Mean:      {mi_mean:>15,.0f} KB")
        print(f"   Difference:           {diff:>15,.0f} KB ({diff_pct:.2f}%)")
        
        if pm_mean > mi_mean:
            print(f"   Process Monitor captures {((pm_mean/mi_mean - 1) * 100):.2f}% more PSS on average")
        else:
            print(f"   Meminsight captures {((mi_mean/pm_mean - 1) * 100):.2f}% more PSS on average")
    
    print("\n" + "=" * 70)

def main():
    parser = argparse.ArgumentParser(
        description='Analyze and compare PSS data from Process Monitor and Meminsight',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Basic usage with paths
  python analyze_pss.py --js results.js --csv meminsight/
  
  # With full paths
  python analyze_pss.py \\
    --js /path/to/results.js \\
    --csv /path/to/meminsight/
  
  # With custom output
  python analyze_pss.py \\
    --js results.js \\
    --csv meminsight/ \\
    --output my_plot.png
        """
    )
    
    parser.add_argument(
        '--js',
        type=str,
        required=True,
        help='Path to results.js file from Process Monitor'
    )
    
    parser.add_argument(
        '--csv',
        type=str,
        required=True,
        help='Path to directory containing meminsight CSV files'
    )
    
    parser.add_argument(
        '--output', '-o',
        type=str,
        default=None,
        help='Output path for the plot (default: pss_comparison.png)'
    )
    
    args = parser.parse_args()
    
    # Validate paths
    js_path = Path(args.js)
    csv_dir = Path(args.csv)
    
    print("=" * 70)
    print("PSS ANALYSIS: Process Monitor vs Meminsight")
    print("=" * 70)
    
    # Check results.js
    print("\n1. Validating results.js path...")
    if not js_path.exists():
        print(f"   ✗ Error: File not found: {js_path}")
        sys.exit(1)
    if not js_path.is_file():
        print(f"   ✗ Error: Not a file: {js_path}")
        sys.exit(1)
    print(f"   ✓ Found: {js_path.absolute()}")
    
    # Check meminsight directory
    print("\n2. Validating meminsight directory...")
    if not csv_dir.exists():
        print(f"   ✗ Error: Directory not found: {csv_dir}")
        sys.exit(1)
    if not csv_dir.is_dir():
        print(f"   ✗ Error: Not a directory: {csv_dir}")
        sys.exit(1)
    print(f"   ✓ Found: {csv_dir.absolute()}")
    
    # Process results.js
    print("\n3. Processing Process Monitor data...")
    results = extract_results_from_js(js_path)
    if results:
        pm_df = process_results_js(results)
        if not pm_df.empty:
            print(f"   ✓ Extracted {len(pm_df)} process records with PSS data")
            print(f"   Time range: {pm_df['timestamp'].min()} to {pm_df['timestamp'].max()}")
            total_pss = pm_df.groupby(pm_df['timestamp'].dt.floor('S'))['pss_kb'].sum().sum()
            print(f"   Total PSS (aggregated): {total_pss:,.0f} KB")
        else:
            print("   ⚠ Warning: No PSS data found in results.js")
    else:
        print("   ✗ Error: Could not parse results.js")
        pm_df = pd.DataFrame()
    
    # Process meminsight files
    print("\n4. Processing Meminsight CSV files...")
    mi_df = process_meminsight_files(csv_dir)
    if not mi_df.empty:
        print(f"   Time range: {mi_df['timestamp'].min()} to {mi_df['timestamp'].max()}")
    
    # Generate plot
    print("\n5. Generating comparison plot...")
    if not pm_df.empty or not mi_df.empty:
        output_path = args.output if args.output else 'pss_comparison.png'
        plot_pss_comparison(pm_df, mi_df, output_path)
        print_statistics(pm_df, mi_df)
    else:
        print("   ✗ Error: No data available to plot")
        sys.exit(1)

if __name__ == '__main__':
    main()