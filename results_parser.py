import json
import argparse
import os.path as path


def parse(filename):
    with open(filename) as results_file:
        results = results_file.read()
        results = results.replace('let results = ', '')
        results = results.replace(';', '')

        results_json = json.loads(results)

        stats = results_json['stats']

        print("")
        print("Processes per group")
        print("-----------------------")

        groups = []

        for group in results_json['groups']:
            processes_in_group = results_json['processes']
            tmp = [x for x in processes_in_group if x['group'] == group['id']]

            unique_child_procs = {}
            for proc in tmp:
                name = proc['content']

                if unique_child_procs.get(name):
                    unique_child_procs[name] += 1
                else:
                    unique_child_procs[name] = 1

            g = {}
            g['name'] = group['content']
            g['frequency'] = len(tmp)
            g['unique_children'] = dict(sorted(unique_child_procs.items(), key=lambda item: item[1], reverse=True))

            groups.append(g)

        for group in sorted(groups, key=lambda k: k['frequency'], reverse=True):
            print(f"{group['name']}: {group['frequency']}")
            for child in group['unique_children']:
                print(f"\t|- {child}: {group['unique_children'][child]} ({round(group['unique_children'][child] / group['frequency'] * 100, 2)}%)")


        print("Top systemd services")
        print("-----------------------")
        for service in sorted(stats['services'], key=lambda k: k['frequency'], reverse=True):
            print(f"{service['serviceName']}: {service['frequency']}")

        print("")
        print("Top unique processes")
        print("-----------------------")
        for process in sorted(stats['processes'], key=lambda k: k['frequency'], reverse=True):
            print(f"{process['process']}: {process['frequency']}")
        
        # Check if any process has memory/CPU data
        processes_with_mem_data = [p for p in results_json['processes'] 
                                   if 'pss' in p or 'rss' in p or 'utime' in p or 'stime' in p]

        if processes_with_mem_data:
            print("")
            print("Process Memory & CPU Statistics")
            print("-----------------------")
            
            # Group by process name
            process_mem_stats = {}
            for proc in processes_with_mem_data:
                proc_name = proc.get('content', 'Unknown')
                if proc_name not in process_mem_stats:
                    process_mem_stats[proc_name] = []
                
                # Convert jiffies to milliseconds (assuming 100 Hz clock, 1 jiffy = 10 ms)
                # Most Linux systems use 100 Hz, but can be 1000 Hz on some systems
                # Using 100 Hz as default: 1 jiffy = 10 ms
                CLOCK_TICKS_PER_SEC = 100
                utime_ms = (proc.get('utime', 0) * 1000) // CLOCK_TICKS_PER_SEC if 'utime' in proc else None
                stime_ms = (proc.get('stime', 0) * 1000) // CLOCK_TICKS_PER_SEC if 'stime' in proc else None
                
                stat_entry = {
                    'pid': proc.get('pid', 'N/A'),
                    'pss': proc.get('pss'),
                    'swapPss': proc.get('swapPss'),
                    'rss': proc.get('rss'),
                    'utime_ms': utime_ms,
                    'stime_ms': stime_ms
                }
                process_mem_stats[proc_name].append(stat_entry)
            
            # Sort by process name, then by PID
            for proc_name in sorted(process_mem_stats.keys()):
                entries = sorted(process_mem_stats[proc_name], key=lambda x: x['pid'])
                print(f"Process Name: {proc_name}")
                
                for entry in entries:
                    stats_parts = []
                    stats_parts.append(f"PID: {entry['pid']}")
                    
                    if entry['pss'] is not None:
                        stats_parts.append(f"PSS: {entry['pss']} KB")
                    if entry['swapPss'] is not None:
                        stats_parts.append(f"SwapPSS: {entry['swapPss']} KB")
                    if entry['rss'] is not None:
                        stats_parts.append(f"RSS: {entry['rss']} KB")
                    if entry['utime_ms'] is not None:
                        stats_parts.append(f"User CPU: {entry['utime_ms']} ms")
                    if entry['stime_ms'] is not None:
                        stats_parts.append(f"System CPU: {entry['stime_ms']} ms")
                    
                    print(f"  - {' | '.join(stats_parts)}")
                print("")


def main():
    parser = argparse.ArgumentParser(prog='ProcessMonitor results parser',
                                     description='Tool to quickly parse the results from ProcessMonitor without needing to load the timeline')
    parser.add_argument('filename', help="Path to the results file generated by ProcessMonitor")
    args = parser.parse_args()

    if not path.isfile(args.filename):
        print(f"Cannot find file {args.filename}")
        return

    parse(args.filename)


if __name__ == "__main__":
    main()
