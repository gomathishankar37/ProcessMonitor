#!/usr/bin/env python3
import json
import argparse
import os.path as path
import csv
from datetime import datetime
from collections import defaultdict


def parse_results_file(filename):
    """Parse the results.js file and return JSON data."""
    with open(filename) as results_file:
        results = results_file.read()
        results = results.replace('let results = ', '')
        results = results.replace(';', '')
        return json.loads(results)


def jiffies_to_ms(jiffies, clock_ticks_per_sec=100):
    """Convert jiffies to milliseconds."""
    return round((jiffies * 1000) / clock_ticks_per_sec)


def format_timestamp(timestamp_ms):
    """Convert milliseconds timestamp to human-readable format."""
    return datetime.fromtimestamp(timestamp_ms / 1000).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]


def calculate_frequencies(results_json):
    """Calculate frequency statistics for processes."""
    total_duration_ms = results_json['end'] - results_json['start']
    total_process_count = len(results_json['processes'])
    
    # Group processes by name
    process_groups = defaultdict(list)
    for proc in results_json['processes']:
        process_name = proc['content']
        process_groups[process_name].append(proc)
    
    # Calculate statistics for each process instance
    process_stats = []
    
    for proc in results_json['processes']:
        process_name = proc['content']
        duration_ms = proc.get('end', proc['start']) - proc['start']
        
        # Count-based frequency (this instance as part of all processes)
        count_freq_all = (1 / total_process_count) * 100 if total_process_count > 0 else 0
        
        # Count-based frequency (how many times this process name appears)
        same_name_count = len(process_groups[process_name])
        count_freq_name = (same_name_count / total_process_count) * 100 if total_process_count > 0 else 0
        
        # Time-based frequency (this instance duration vs total capture time)
        time_freq_instance = (duration_ms / total_duration_ms) * 100 if total_duration_ms > 0 else 0
        
        # Time-based frequency (all instances of this process name vs total capture time)
        total_name_duration = sum(p.get('end', p['start']) - p['start'] for p in process_groups[process_name])
        time_freq_name = (total_name_duration / total_duration_ms) * 100 if total_duration_ms > 0 else 0
        
        stats = {
            'process': proc,
            'duration_ms': duration_ms,
            'count_freq_all': count_freq_all,
            'count_freq_name': count_freq_name,
            'time_freq_instance': time_freq_instance,
            'time_freq_name': time_freq_name,
            'same_name_count': same_name_count,
            'same_name_total_duration': total_name_duration
        }
        process_stats.append(stats)
    
    return process_stats, total_duration_ms, total_process_count


def print_process_details(stats_list, sort_by='start_time'):
    """Print detailed process information to console."""
    # Sort based on criteria
    if sort_by == 'start_time':
        stats_list = sorted(stats_list, key=lambda x: x['process']['start'])
    elif sort_by == 'duration':
        stats_list = sorted(stats_list, key=lambda x: x['duration_ms'], reverse=True)
    elif sort_by == 'name':
        stats_list = sorted(stats_list, key=lambda x: x['process']['content'])
    elif sort_by == 'pss':
        stats_list = sorted(stats_list, key=lambda x: x['process'].get('pss', 0), reverse=True)
    elif sort_by == 'rss':
        stats_list = sorted(stats_list, key=lambda x: x['process'].get('rss', 0), reverse=True)
    
    print("\n" + "="*100)
    print("DETAILED PROCESS LISTING")
    print("="*100)
    
    for idx, stats in enumerate(stats_list, 1):
        proc = stats['process']
        
        print(f"\n[{idx}/{len(stats_list)}] Process Instance")
        print("-" * 100)
        
        print(f"PID: {proc.get('pid', 'N/A')}")
        print(f"Name: {proc.get('content', 'N/A')}")
        print(f"Command Line: {proc.get('fullCommandLine', 'N/A')}")
        print(f"Parent Command Line: {proc.get('parentCommandLine', 'N/A')}")
        print(f"Grandparent Command Line: {proc.get('grandparentCommandLine', 'N/A')}")
        
        # Time information
        start_time = format_timestamp(proc['start'])
        end_time = format_timestamp(proc.get('end', proc['start']))
        print(f"Start Time: {start_time} ({proc['start']} ms)")
        print(f"End Time: {end_time} ({proc.get('end', proc['start'])} ms)")
        print(f"Duration: {stats['duration_ms']} ms")
        
        print(f"Exit Code: {proc.get('exitCode', 'N/A')}")
        print(f"Systemd Service: {proc.get('systemdService', 'N/A')}")
        
        # Memory information
        pss = proc.get('pss', None)
        swap_pss = proc.get('swapPss', None)
        rss = proc.get('rss', None)
        
        print(f"PSS (KB): {pss if pss is not None else 'N/A'}")
        print(f"SwapPSS (KB): {swap_pss if swap_pss is not None else 'N/A'}")
        print(f"RSS (KB): {rss if rss is not None else 'N/A'}")
        
        # CPU time
        if 'utime' in proc and 'stime' in proc:
            utime_ms = jiffies_to_ms(proc['utime'])
            stime_ms = jiffies_to_ms(proc['stime'])
            total_cpu_ms = utime_ms + stime_ms
            print(f"CPU Time (ms): {total_cpu_ms} ms (stime: {stime_ms} ms, utime: {utime_ms} ms)")
        else:
            print(f"CPU Time (ms): N/A")
        
        # Frequency information
        print(f"\nFrequency Statistics:")
        print(f"  Time-based (this instance): {stats['time_freq_instance']:.4f}%")
        print(f"  Time-based (all '{proc['content']}' instances): {stats['time_freq_name']:.4f}% (cumulative duration: {stats['same_name_total_duration']} ms)")
        print(f"  Count-based (occurrences of '{proc['content']}'): {stats['same_name_count']} times ({stats['count_freq_name']:.4f}% of all processes)")
        print(f"  Count-based (this instance): {stats['count_freq_all']:.4f}% (1 out of all processes)")


def print_summary_statistics(stats_list, total_duration_ms, total_process_count):
    """Print summary statistics grouped by process name."""
    # Group by process name
    summary = defaultdict(lambda: {
        'count': 0,
        'total_duration_ms': 0,
        'pss_values': [],
        'rss_values': [],
        'swap_pss_values': [],
        'cpu_time_values': [],
        'pids': [],
        'command_lines': set(),
        'parents': set(),
        'services': set()
    })
    
    for stats in stats_list:
        proc = stats['process']
        name = proc['content']
        
        summary[name]['count'] += 1
        summary[name]['total_duration_ms'] += stats['duration_ms']
        summary[name]['pids'].append(proc.get('pid', 'N/A'))
        
        if 'fullCommandLine' in proc:
            summary[name]['command_lines'].add(proc['fullCommandLine'])
        if 'parentCommandLine' in proc:
            summary[name]['parents'].add(proc['parentCommandLine'])
        if 'systemdService' in proc:
            summary[name]['services'].add(proc['systemdService'])
        
        if 'pss' in proc:
            summary[name]['pss_values'].append(proc['pss'])
        if 'rss' in proc:
            summary[name]['rss_values'].append(proc['rss'])
        if 'swapPss' in proc:
            summary[name]['swap_pss_values'].append(proc['swapPss'])
        
        if 'utime' in proc and 'stime' in proc:
            cpu_ms = jiffies_to_ms(proc['utime'] + proc['stime'])
            summary[name]['cpu_time_values'].append(cpu_ms)
    
    # Sort by count (most frequent first)
    sorted_summary = sorted(summary.items(), key=lambda x: x[1]['count'], reverse=True)
    
    print("\n" + "="*100)
    print("SUMMARY STATISTICS (Grouped by Process Name)")
    print("="*100)
    
    for name, data in sorted_summary:
        print(f"\n{'='*100}")
        print(f"Process Name: {name}")
        print(f"{'='*100}")
        
        # Occurrence statistics
        print(f"\nOccurrence Statistics:")
        print(f"  Total Occurrences: {data['count']}")
        print(f"  Frequency (count-based): {(data['count'] / total_process_count * 100):.4f}% of all processes")
        print(f"  Frequency (time-based): {(data['total_duration_ms'] / total_duration_ms * 100):.4f}% of capture time")
        print(f"  Cumulative Duration: {data['total_duration_ms']} ms")
        print(f"  Average Duration per Instance: {data['total_duration_ms'] / data['count']:.2f} ms")
        
        # PIDs
        print(f"\nPIDs: {', '.join(map(str, data['pids']))}")
        
        # Command lines
        if data['command_lines']:
            print(f"\nCommand Line(s):")
            for cmd in data['command_lines']:
                print(f"  - {cmd}")
        
        # Parent processes
        if data['parents']:
            print(f"\nParent Process(es):")
            for parent in data['parents']:
                print(f"  - {parent}")
        
        # Services
        if data['services']:
            print(f"\nSystemd Service(s): {', '.join(data['services'])}")
        
        # Memory statistics
        if data['pss_values']:
            avg_pss = sum(data['pss_values']) / len(data['pss_values'])
            min_pss = min(data['pss_values'])
            max_pss = max(data['pss_values'])
            print(f"\nPSS (KB) - Avg: {avg_pss:.2f}, Min: {min_pss}, Max: {max_pss}")
        
        if data['rss_values']:
            avg_rss = sum(data['rss_values']) / len(data['rss_values'])
            min_rss = min(data['rss_values'])
            max_rss = max(data['rss_values'])
            print(f"RSS (KB) - Avg: {avg_rss:.2f}, Min: {min_rss}, Max: {max_rss}")
        
        if data['swap_pss_values']:
            avg_swap = sum(data['swap_pss_values']) / len(data['swap_pss_values'])
            min_swap = min(data['swap_pss_values'])
            max_swap = max(data['swap_pss_values'])
            print(f"SwapPSS (KB) - Avg: {avg_swap:.2f}, Min: {min_swap}, Max: {max_swap}")
        
        if data['cpu_time_values']:
            avg_cpu = sum(data['cpu_time_values']) / len(data['cpu_time_values'])
            total_cpu = sum(data['cpu_time_values'])
            min_cpu = min(data['cpu_time_values'])
            max_cpu = max(data['cpu_time_values'])
            print(f"CPU Time (ms) - Total: {total_cpu}, Avg: {avg_cpu:.2f}, Min: {min_cpu}, Max: {max_cpu}")


def export_to_csv(stats_list, output_filename):
    """Export detailed process information to CSV."""
    fieldnames = [
        'PID', 'Name', 'Command_Line', 'Parent_Command_Line', 'Grandparent_Command_Line',
        'Start_Time_Readable', 'Start_Time_Ms', 'End_Time_Readable', 'End_Time_Ms',
        'Duration_Ms', 'Exit_Code', 'Systemd_Service',
        'PSS_KB', 'SwapPSS_KB', 'RSS_KB', 'CPU_Time_Ms', 'CPU_Stime_Ms', 'CPU_Utime_Ms',
        'Freq_Time_Instance_Pct', 'Freq_Time_AllInstances_Pct', 'Freq_Count_SameName',
        'Freq_Count_Pct', 'Same_Name_Total_Duration_Ms'
    ]
    
    with open(output_filename, 'w', newline='', encoding='utf-8') as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        
        for stats in stats_list:
            proc = stats['process']
            
            # CPU time calculation
            cpu_time_ms = 'N/A'
            cpu_stime_ms = 'N/A'
            cpu_utime_ms = 'N/A'
            if 'utime' in proc and 'stime' in proc:
                cpu_utime_ms = jiffies_to_ms(proc['utime'])
                cpu_stime_ms = jiffies_to_ms(proc['stime'])
                cpu_time_ms = cpu_utime_ms + cpu_stime_ms
            
            row = {
                'PID': proc.get('pid', 'N/A'),
                'Name': proc.get('content', 'N/A'),
                'Command_Line': proc.get('fullCommandLine', 'N/A'),
                'Parent_Command_Line': proc.get('parentCommandLine', 'N/A'),
                'Grandparent_Command_Line': proc.get('grandparentCommandLine', 'N/A'),
                'Start_Time_Readable': format_timestamp(proc['start']),
                'Start_Time_Ms': proc['start'],
                'End_Time_Readable': format_timestamp(proc.get('end', proc['start'])),
                'End_Time_Ms': proc.get('end', proc['start']),
                'Duration_Ms': stats['duration_ms'],
                'Exit_Code': proc.get('exitCode', 'N/A'),
                'Systemd_Service': proc.get('systemdService', 'N/A'),
                'PSS_KB': proc.get('pss', 'N/A'),
                'SwapPSS_KB': proc.get('swapPss', 'N/A'),
                'RSS_KB': proc.get('rss', 'N/A'),
                'CPU_Time_Ms': cpu_time_ms,
                'CPU_Stime_Ms': cpu_stime_ms,
                'CPU_Utime_Ms': cpu_utime_ms,
                'Freq_Time_Instance_Pct': f"{stats['time_freq_instance']:.4f}",
                'Freq_Time_AllInstances_Pct': f"{stats['time_freq_name']:.4f}",
                'Freq_Count_SameName': stats['same_name_count'],
                'Freq_Count_Pct': f"{stats['count_freq_name']:.4f}",
                'Same_Name_Total_Duration_Ms': stats['same_name_total_duration']
            }
            writer.writerow(row)
    
    print(f"\n✓ CSV exported to: {output_filename}")


def main():
    parser = argparse.ArgumentParser(
        prog='Detailed ProcessMonitor results parser',
        description='Detailed parser for ProcessMonitor results with frequency and memory statistics',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s results.js                              # Console output sorted by start time
  %(prog)s results.js --csv output.csv             # Export to CSV
  %(prog)s results.js --summary                    # Show summary statistics
  %(prog)s results.js --sort-by duration           # Sort by duration
  %(prog)s results.js --sort-by pss --csv out.csv  # Sort by PSS and export
        """
    )
    
    parser.add_argument('filename', help="Path to the results.js file generated by ProcessMonitor")
    parser.add_argument('--csv', metavar='OUTPUT', help="Export detailed results to CSV file")
    parser.add_argument('--summary', action='store_true', help="Show summary statistics grouped by process name")
    parser.add_argument('--sort-by', choices=['start_time', 'duration', 'name', 'pss', 'rss'],
                        default='start_time', help="Sort output by field (default: start_time)")
    parser.add_argument('--no-details', action='store_true', 
                        help="Skip detailed process listing (useful with --summary or --csv only)")
    
    args = parser.parse_args()
    
    if not path.isfile(args.filename):
        print(f"Error: Cannot find file {args.filename}")
        return 1
    
    # Parse results
    results_json = parse_results_file(args.filename)
    stats_list, total_duration_ms, total_process_count = calculate_frequencies(results_json)
    
    # Print header information
    print("\n" + "="*100)
    print("PROCESS MONITOR RESULTS ANALYSIS")
    print("="*100)
    print(f"Capture Start: {format_timestamp(results_json['start'])} ({results_json['start']} ms)")
    print(f"Capture End: {format_timestamp(results_json['end'])} ({results_json['end']} ms)")
    print(f"Total Capture Duration: {total_duration_ms} ms ({total_duration_ms / 1000:.2f} seconds)")
    print(f"Total Process Instances: {total_process_count}")
    print(f"Unique Process Names: {len(set(p['content'] for p in results_json['processes']))}")
    
    # Sort stats for display
    if args.sort_by == 'start_time':
        stats_list = sorted(stats_list, key=lambda x: x['process']['start'])
    elif args.sort_by == 'duration':
        stats_list = sorted(stats_list, key=lambda x: x['duration_ms'], reverse=True)
    elif args.sort_by == 'name':
        stats_list = sorted(stats_list, key=lambda x: x['process']['content'])
    elif args.sort_by == 'pss':
        stats_list = sorted(stats_list, key=lambda x: x['process'].get('pss', 0), reverse=True)
    elif args.sort_by == 'rss':
        stats_list = sorted(stats_list, key=lambda x: x['process'].get('rss', 0), reverse=True)
    
    # Print detailed listing unless --no-details is specified
    if not args.no_details:
        print_process_details(stats_list, args.sort_by)
    
    # Print summary if requested
    if args.summary:
        print_summary_statistics(stats_list, total_duration_ms, total_process_count)
    
    # Export to CSV if requested
    if args.csv:
        export_to_csv(stats_list, args.csv)
    
    print("\n" + "="*100)
    print("Analysis complete!")
    print("="*100 + "\n")
    
    return 0


if __name__ == "__main__":
    exit(main())

