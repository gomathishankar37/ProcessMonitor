#!/usr/bin/env python3
"""
Analyze processes spawned by systemd services from results.js file.
Lists scripts and binaries with their spawn counts, memory usage, and parent services.
"""

import json
import re
import argparse
from collections import defaultdict
from pathlib import Path


def load_results_js(filepath):
    """Load and parse the results.js file."""
    with open(filepath, 'r') as f:
        content = f.read()
    
    # Extract the JSON data from the JavaScript variable
    # Format: let results = {...};
    match = re.search(r'let\s+results\s*=\s*({.*});', content, re.DOTALL)
    if not match:
        raise ValueError("Could not find results variable in file")
    
    json_str = match.group(1)
    return json.loads(json_str)


def is_script(name):
    """Check if a process name is a shell script."""
    return name.endswith('.sh')


def is_binary(name):
    """Check if a process name is a binary (has full path but not .sh)."""
    # Binary: starts with / and doesn't end with .sh
    return name.startswith('/') and not name.endswith('.sh')


def count_spawned_by_script_or_binary(script_name, processes):
    """
    Count all processes where the given script/binary appears in parent or grandparent.
    This gives a more accurate count of what a script is responsible for spawning.
    
    Args:
        script_name: The script or binary name to search for
        processes: List of all process entries
        
    Returns:
        tuple: (count, total_pss, total_rss, child_processes_list)
    """
    spawned = []
    total_pss = 0
    total_rss = 0
    
    for proc in processes:
        parent = proc.get('parentCommandLine', '')
        grandparent = proc.get('grandparentCommandLine', '')
        
        # Check if script appears in parent or grandparent command line
        if script_name in parent or script_name in grandparent:
            spawned.append(proc)
            
            # Add memory if available
            pss, rss = get_memory_stats(proc)
            total_pss += pss
            total_rss += rss
    
    return len(spawned), total_pss, total_rss, spawned


def get_memory_stats(process):
    """Extract PSS and RSS memory values from a process entry.
    
    Args:
        process: Process dictionary with optional 'pss' and 'rss' fields
        
    Returns:
        tuple: (pss, rss) values in KB, both default to 0 if not present
    """
    pss = process.get('pss', 0)
    rss = process.get('rss', 0)
    return pss, rss


def normalize_service_name(service):
    """Normalize service name to N/A if empty or unknown.
    
    Args:
        service: Service name string
        
    Returns:
        str: Normalized service name, or 'N/A' if invalid
    """
    if not service or service in ('Unknown', 'None', ''):
        return 'N/A'
    return service


def aggregate_process_metrics(processes_list):
    """Aggregate memory metrics from a list of process entries.
    
    Args:
        processes_list: List of process dictionaries
        
    Returns:
        tuple: (total_pss, total_rss, process_count)
    """
    total_pss = 0
    total_rss = 0
    count = 0
    
    for proc in processes_list:
        pss, rss = get_memory_stats(proc)
        total_pss += pss
        total_rss += rss
        count += 1
    
    return total_pss, total_rss, count


def analyze_scripts(results):
    """Analyze shell scripts and their spawned processes.
    
    This function handles two types of scripts:
    1. Process scripts: Short-lived individual executions found in processes array
    2. Group scripts: Long-running parent processes found in groups array
    
    For Process scripts, we calculate average memory from all executions.
    For spawned processes, we count any process where the script appears in parent or grandparent.
    """
    processes = results.get('processes', [])
    groups = results.get('groups', [])
    
    script_data = defaultdict(lambda: {
        'spawn_count': 0,
        'total_pss': 0,
        'total_rss': 0,
        'pss_count': 0,  # Track number of instances with PSS for averaging
        'rss_count': 0,  # Track number of instances with RSS for averaging
        'process_count': 0,  # Track total number of process instances
        'instance_count': 0,  # Frequency count - how many times this script ran
        'services': set(),
        'instances': [],
        'types': set()
    })
    
    # First pass: Collect all script names (both from processes and groups)
    all_scripts = set()
    
    # === Collect Process Scripts (individual short-lived executions) ===
    for proc in processes:
        content = proc.get('content', '')
        
        if is_script(content):
            all_scripts.add(content)
            service = normalize_service_name(proc.get('systemdService', 'Unknown'))
            
            # Get memory stats for this process instance
            pss, rss = get_memory_stats(proc)
            
            # Accumulate metrics
            if pss > 0:
                script_data[content]['total_pss'] += pss
                script_data[content]['pss_count'] += 1
            if rss > 0:
                script_data[content]['total_rss'] += rss
                script_data[content]['rss_count'] += 1
            script_data[content]['process_count'] += 1
            script_data[content]['instance_count'] += 1  # Count each instance
            script_data[content]['services'].add(service)
            script_data[content]['types'].add('Process')
    
    # === Collect Group Scripts ===
    for group in groups:
        group_name = group.get('content', '') or group.get('id', '')
        if is_script(group_name):
            all_scripts.add(group_name)
            script_data[group_name]['types'].add('Group')
    
    # Second pass: For each script, count ALL processes spawned by it (parent or grandparent match)
    for script_name in all_scripts:
        spawn_count, spawn_pss, spawn_rss, spawned_procs = count_spawned_by_script_or_binary(
            script_name, processes
        )
        
        # Add spawn count and spawned process memory
        script_data[script_name]['spawn_count'] = spawn_count
        
        # For Group scripts, add the spawned memory to totals
        # For Process scripts, keep their own memory separate from spawned
        if 'Group' in script_data[script_name]['types']:
            # Group scripts show total of all spawned processes
            script_data[script_name]['total_pss'] += spawn_pss
            script_data[script_name]['total_rss'] += spawn_rss
        
        # Collect services from spawned processes
        for proc in spawned_procs:
            service = normalize_service_name(proc.get('systemdService', 'Unknown'))
            script_data[script_name]['services'].add(service)
    
    return script_data


def analyze_binaries(results):
    """Analyze binaries and their spawned processes.
    
    This function handles two types of binaries:
    1. Process binaries: Short-lived individual executions found in processes array
    2. Group binaries: Long-running parent processes found in groups array
    
    For Process binaries, we calculate average memory from all executions.
    For spawned processes, we count any process where the binary appears in parent or grandparent.
    """
    processes = results.get('processes', [])
    groups = results.get('groups', [])
    
    binary_data = defaultdict(lambda: {
        'spawn_count': 0,
        'total_pss': 0,
        'total_rss': 0,
        'pss_count': 0,  # Track number of instances with PSS for averaging
        'rss_count': 0,  # Track number of instances with RSS for averaging
        'process_count': 0,  # Track total number of process instances
        'instance_count': 0,  # Frequency count - how many times this binary ran
        'services': set(),
        'instances': [],
        'types': set()
    })
    
    # First pass: Collect all binary names (both from processes and groups)
    all_binaries = set()
    
    # === Collect Process Binaries (individual short-lived executions) ===
    for proc in processes:
        content = proc.get('content', '')
        
        if is_binary(content):
            all_binaries.add(content)
            service = normalize_service_name(proc.get('systemdService', 'Unknown'))
            
            # Get memory stats for this process instance
            pss, rss = get_memory_stats(proc)
            
            # Accumulate metrics
            if pss > 0:
                binary_data[content]['total_pss'] += pss
                binary_data[content]['pss_count'] += 1
            if rss > 0:
                binary_data[content]['total_rss'] += rss
                binary_data[content]['rss_count'] += 1
            binary_data[content]['process_count'] += 1
            binary_data[content]['instance_count'] += 1  # Count each instance
            binary_data[content]['services'].add(service)
            binary_data[content]['types'].add('Process')
    
    # === Collect Group Binaries ===
    for group in groups:
        group_name = group.get('content', '') or group.get('id', '')
        if is_binary(group_name):
            all_binaries.add(group_name)
            binary_data[group_name]['types'].add('Group')
    
    # Second pass: For each binary, count ALL processes spawned by it (parent or grandparent match)
    for binary_name in all_binaries:
        spawn_count, spawn_pss, spawn_rss, spawned_procs = count_spawned_by_script_or_binary(
            binary_name, processes
        )
        
        # Add spawn count and spawned process memory
        binary_data[binary_name]['spawn_count'] = spawn_count
        
        # For Group binaries, add the spawned memory to totals
        # For Process binaries, keep their own memory separate from spawned
        if 'Group' in binary_data[binary_name]['types']:
            # Group binaries show total of all spawned processes
            binary_data[binary_name]['total_pss'] += spawn_pss
            binary_data[binary_name]['total_rss'] += spawn_rss
        
        # Collect services from spawned processes
        for proc in spawned_procs:
            service = normalize_service_name(proc.get('systemdService', 'Unknown'))
            binary_data[binary_name]['services'].add(service)
    
    return binary_data


def format_memory_value(info, mem_type='pss'):
    """Format memory values for display.
    
    For Process types: Calculate and show average per execution
    For Group types: Show total aggregated memory
    
    Args:
        info: Dictionary with memory data
        mem_type: 'pss' or 'rss'
        
    Returns:
        str: Formatted memory string with appropriate label
    """
    types = info.get('types', set())
    total_mem = info.get(f'total_{mem_type}', 0)
    mem_count_key = f'{mem_type}_count'
    mem_count = info.get(mem_count_key, info.get('process_count', 0))
    
    if total_mem == 0:
        return "N/A"
    
    # For Group scripts, show total
    if 'Group' in types and 'Process' not in types:
        return f"{total_mem:,}"
    
    # For Process scripts, show average (only divide by instances that had this memory type)
    if 'Process' in types and mem_count > 0:
        avg_mem = total_mem / mem_count
        return f"{avg_mem:,.0f} (avg)"
    
    # Mixed or unknown
    if 'Group' in types and 'Process' in types:
        # Group takes precedence for mixed types
        return f"{total_mem:,}"
    
    return f"{total_mem:,}"


def print_table(data, title):
    """Print formatted table with memory statistics.
    
    Displays PSS/RSS values differently based on type:
    - Group: Total aggregated memory from all child processes
    - Process: Average memory per execution
    """
    if not data:
        print(f"\nNo {title} found.\n")
        return
    
    print(f"\n{'='*170}")
    print(f"{title}")
    print(f"{'='*170}")
    
    # Sort by spawn count (descending)
    sorted_items = sorted(data.items(), key=lambda x: x[1]['spawn_count'], reverse=True)
    
    # Print header
    header = f"{'Script/Binary Name':<50} | {'Type':<15} | {'Instances':<10} | {'Spawned':<10} | {'PSS (KB)':<15} | {'RSS (KB)':<15} | {'Systemd Service':<30}"
    print(header)
    print('-' * 170)
    
    # Print rows
    for name, info in sorted_items:
        spawn_count = info['spawn_count']
        instance_count = info.get('instance_count', 0)
        services = ', '.join(sorted(info['services']))
        types = ', '.join(sorted(info.get('types', {'Unknown'})))
        
        # Handle long names
        display_name = name if len(name) <= 50 else name[:47] + '...'
        
        # Format PSS/RSS with appropriate values (avg for Process, total for Group)
        pss_str = format_memory_value(info, 'pss')
        rss_str = format_memory_value(info, 'rss')
        
        # Handle long service names
        if len(services) > 30:
            services = services[:27] + '...'
        
        print(f"{display_name:<50} | {types:<15} | {instance_count:<10} | {spawn_count:<10} | {pss_str:<15} | {rss_str:<15} | {services:<30}")
    
    print(f"{'='*170}\n")
    print(f"Total entries: {len(sorted_items)}")
    print()


def print_markdown_table(data, title):
    """Print table in Markdown format with memory statistics.
    
    Displays PSS/RSS values differently based on type:
    - Group: Total aggregated memory from all child processes
    - Process: Average memory per execution (marked with 'avg')
    """
    if not data:
        print(f"\n## {title}\n\nNo data found.\n")
        return
    
    print(f"\n## {title}\n")
    
    # Sort by spawn count (descending)
    sorted_items = sorted(data.items(), key=lambda x: x[1]['spawn_count'], reverse=True)
    
    # Print header
    print(f"| {'Script/Binary Name':<45} | {'Type':<15} | {'Instances':<10} | {'Spawned':<16} | {'PSS (KB)':<15} | {'RSS (KB)':<15} | {'Systemd Service':<28} |")
    print(f"|{'-'*47}|{'-'*17}|{'-'*12}|{'-'*18}|{'-'*17}|{'-'*17}|{'-'*30}|")
    
    # Print rows
    for name, info in sorted_items:
        spawn_count = info['spawn_count']
        instance_count = info.get('instance_count', 0)
        services = ', '.join(sorted(info['services']))
        types = ', '.join(sorted(info.get('types', {'Unknown'})))
        
        # Format PSS/RSS with appropriate values (avg for Process, total for Group)
        pss_str = format_memory_value(info, 'pss')
        rss_str = format_memory_value(info, 'rss')
        
        print(f"| {name:<45} | {types:<15} | {instance_count:<10} | {spawn_count:<16} | {pss_str:<15} | {rss_str:<15} | {services:<28} |")
    
    print(f"\n**Total entries:** {len(sorted_items)}\n")


def main():
    parser = argparse.ArgumentParser(
        description='Analyze processes spawned by systemd services',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --scripts 2hr-results.js
  %(prog)s --binaries 2hr-results.js
  %(prog)s --all 2hr-results.js
  %(prog)s --scripts --markdown 2hr-results.js
        """
    )
    
    parser.add_argument('file', help='Path to results.js file')
    parser.add_argument('--scripts', action='store_true', help='Show script analysis')
    parser.add_argument('--binaries', action='store_true', help='Show binary analysis')
    parser.add_argument('--all', action='store_true', help='Show both scripts and binaries')
    parser.add_argument('--markdown', action='store_true', help='Output in Markdown format')
    
    args = parser.parse_args()
    
    # Default to --all if nothing specified
    if not (args.scripts or args.binaries or args.all):
        args.all = True
    
    # Load data
    try:
        results = load_results_js(args.file)
    except Exception as e:
        print(f"Error loading file: {e}")
        return 1
    
    print_func = print_markdown_table if args.markdown else print_table
    
    # Analyze and print scripts
    if args.scripts or args.all:
        script_data = analyze_scripts(results)
        print_func(script_data, "Shell Scripts Analysis")
    
    # Analyze and print binaries
    if args.binaries or args.all:
        binary_data = analyze_binaries(results)
        print_func(binary_data, "Binaries Analysis")
    
    return 0


if __name__ == '__main__':
    exit(main())

