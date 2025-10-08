# -*- coding: utf-8 -*-
"""
Created on Mon Jul 14 20:00:58 2025
Author: Rebecca Ye
Updated: Tue Aug 26 2025
Description: The following file analyzes a given judge's scoring marks spread for all judged events in a given USFSA competition.
  1. Prompts user for official USFSA website link to full competition results.
  2. Prompts user to input the judge's full name.
  3. Searches through each event link to verify if judge participated in that event. 
  4. Pulls scoring data file if judge participated in the event.
  4. Returns an excel file summarizing the percentage of marks the judge had within or near the panel range
    (usually 5-9 judges on each event), with one summary per event.

Sample Test:
  1. Run. 
  2. When prompted for html, input: https://ijs.usfigureskating.org/leaderboard/results/2025/36167/index.asp
  3. When prompted for judge name, input: Rebecca Ye
"""

import pandas as pd
import numpy as np
import lxml
from bs4 import BeautifulSoup
import requests as req
import urllib.request
import re
  
# Requesting for the website
webpage = input("Enter html link to scores here: ")
address = webpage.replace("index.asp", "")
Web = req.get(webpage)
  
# Creating a BeautifulSoup object and specifying the parser
S = BeautifulSoup(Web.text, 'lxml')
S = S.find('table', {"id":"daySort"})
  
# Using the prettify method
# print(S.prettify())

my_name = input("Enter official's name here: ")

# Create empty dataframe to publish output stats to
final = pd.DataFrame(columns=["Skater",
                            'Number of Skaters',
                            'Number of Judges',
                            '# of GOEs',
                            'Total # GOEs',
                            '# of GOEs w/in Range',
                            '# of GOEs w/in Range +/- 1',
                            'GOE % w/in Range',
                            'GOE % w/in Range +/- 1',
                            'Panel Component Range',
                            'Composition',
                            'Presentation',
                            'Skating Skills',
                            'TJ Component Range',
                            'TJ Composition',
                            'TJ Presentation',
                            'TJ Skating Skills',
                            '# of Components',
                            'Total # Components',
                            '# Components w/in Range',
                            '# of Components w/in Range +/- .25',
                            'Component % w/in Range',
                            'Component % w/in Range +/- .25'])


for link in S.findAll('a'):
    event_link = address + link.get('href')
    #print(event_link)
    Web_event = req.get(event_link)
    
    # Creating a BeautifulSoup object and specifying the parser
    S_event = BeautifulSoup(Web_event.text, 'lxml')
    
    # Find all matching <table> elements

    table_officials = S_event.find_all('table', {"class":"officials ladies"})

    if not table_officials:
        table_officials = S_event.find_all('table', {"class":"officials men"})
    if not table_officials:
        table_officials = S_event.find_all('table', {"class":"officials pairs"})
    if not table_officials:
        table_officials = S_event.find_all('table', {"class":"officials team"})
    
    soup_event = BeautifulSoup(str(table_officials), 'html.parser')
    
    # Extract rows from tbody
    rows = soup_event.find('tbody').find_all('tr')

    # Convert to dictionary
    officials_dict = {row.find_all('td')[0].text: row.find_all('td')[1].text for row in rows}

    # Convert dictionary to DataFrame
    df = pd.DataFrame(list(officials_dict.items()), columns=['Function', 'Name'])
    df = df.loc[df["Function"].str.startswith("Judge")]
    #print(df)
    # Check if official's name is in the table
    found = False
    
    for judge in df["Name"]:
        if judge.startswith(my_name):
            found = True
            row = df[df["Name"].str.startswith(my_name)]
            
            # Extract judge number from Function (if match found)
            function = row.iloc[0]["Function"]
            match = re.search(r'Judge (\d+)', function)
            judge_id = "J" + match.group(1)
            num_judges = str(len(df["Name"]))
            #print(judge_id)
            break
    
    # Show the DataFrame if found = True
    if found == True:
        
        # Enter Judge detail scores page
        S_detail = S_event.find_all('li', {"class":"judgeDetailRef"})
        
        # Parse HTML
        soup_det = BeautifulSoup(str(S_detail), 'html.parser')
        det_link = soup_det.find('a')
        detail_link = address + det_link.get('href')
        Web_det = req.get(detail_link)
        
        # Creating the ~real~ BeautifulSoup object and specifying the parser
        S_det = BeautifulSoup(Web_det.text, 'lxml')
          
        # Using the prettify method
        # print(S.prettify())

        table_names = S_det.find_all('table', {"class":"sum"})
        table_elm = S_det.find_all('table', {"class": "elm"})

        df_name = pd.read_html(str(table_names))
        names = []
        for df in df_name:
            names.append(df.iloc[0,1].split(', ')[0])

        skaters = pd.read_html(str(table_elm))


        # for i in range(0, len(df_pre)):
        #     if list(df_pre[i].columns)[0] == '#':
        #         skaters.append(df_pre[i])
        #     if list(df_pre[i].columns)[]

        # Create empty dataframe to publish output stats to
        stats = pd.DataFrame(columns=['Number of Judges',
                                    '# of GOEs',
                                    'Total # GOEs',
                                    '# of GOEs w/in Range',
                                    '# of GOEs w/in Range +/- 1',
                                    'GOE % w/in Range',
                                    'GOE % w/in Range +/- 1',
                                    'Panel Component Range',
                                    'Composition',
                                    'Presentation',
                                    'Skating Skills',
                                    'TJ Component Range',
                                    'TJ Composition',
                                    'TJ Presentation',
                                    'TJ Skating Skills',
                                    '# of Components',
                                    'Total # Components',
                                    '# Components w/in Range',
                                    '# of Components w/in Range +/- .25',
                                    'Component % w/in Range',
                                    'Component % w/in Range +/- .25'])


        # Calculate num of GOEs in [min, max] of all other judge GOEs
        ## Input raw_goes
        def num_goes_in_range(raw):
            count = 0
            for i in range(0, num_goes):
                if min_goe[i] <= raw[judge_id][i] <= max_goe[i]:
                    count = count + 1
            return count


        # Calculate num of GOEs in [min-1, max+1] of all other judge GOEs
        ## Input raw_goes
        def num_goes_1(raw):
            count = 0
            for i in range(0, num_goes):
                min_minus_1 = float(min_goe[i]) - 1
                max_plus_1 = float(max_goe[i]) + 1
                if min_minus_1 <= raw[judge_id][i] <= max_plus_1:
                    count = count + 1
            return count

        # Calculate num of components in [min, max] of all other judge components
        ## Input raw_comp
        def num_comp_in_range(raw):
            count = 0
            for i in range(0,3):
                if min_comp[i] <= raw[judge_id][i] <= max_comp[i]:
                    count = count + 1
            return count

        # Calculate num of components in [min-.25, max+.25] of all other judge components
        ## Input raw_comp
        def num_comp_25(raw):
            count = 0
            for i in range(0,3):
                min_minus_25 = float(min_comp[i]) - .25
                max_plus_25 = float(max_comp[i]) + .25
                if min_minus_25 <= raw[judge_id][i] <= max_plus_25:
                    count = count + 1
            return count


        # Generate stats for each skater
        for df in skaters:
            
            result = pd.DataFrame(columns=['Number of Judges',
                                        '# of GOEs',
                                        'Total # GOEs',
                                        '# of GOEs w/in Range',
                                        '# of GOEs w/in Range +/- 1',
                                        'GOE % w/in Range',
                                        'GOE % w/in Range +/- 1',
                                        'Panel Component Range',
                                        'Composition',
                                        'Presentation',
                                        'Skating Skills',
                                        'TJ Component Range',
                                        'TJ Composition',
                                        'TJ Presentation',
                                        'TJ Skating Skills',
                                        '# of Components',
                                        'Total # Components',
                                        '# Components w/in Range',
                                        '# of Components w/in Range +/- .25',
                                        'Component % w/in Range',
                                        'Component % w/in Range +/- .25'])
            
            judge_cols = df.columns[df.columns.str.startswith('J')]
            num_judges = str(num_judges)
            
            raw_scores = (df.loc[:,"J1":"J" + num_judges]).dropna().reset_index().drop(columns="index")
            
            raw_goes = raw_scores.iloc[:-3, :].replace('\U00002013', '-')
            raw_goes.loc[raw_goes['J1'] == '-'] = np.nan
            raw_goes = raw_goes.astype(float)
            raw_comp = raw_scores.iloc[-3:, :].reset_index().drop(columns='index').astype(float)
            
            num_judges = len(raw_scores.columns)
            num_goes = len(raw_scores) - 3
            num_total_goes = num_judges*num_goes
            num_total_comp = num_judges*3
            
            # Find min,max for each element GOE
            min_goe = raw_goes.loc[:,raw_goes.columns != judge_id].min(axis = 1).astype(float)
            max_goe = raw_goes.loc[:,raw_goes.columns != judge_id].max(axis = 1).astype(float)
            
            # Find min,max for each program component
            min_comp = raw_comp.loc[:,raw_comp.columns != judge_id].min(axis = 1).astype(float)
            max_comp = raw_comp.loc[:,raw_comp.columns != judge_id].max(axis = 1).astype(float)
            
            
            result = pd.DataFrame({"Number of Judges": num_judges,
                                "# of GOEs": num_goes,
                                'Total # GOEs': num_total_goes,
                                '# of GOEs w/in Range': num_goes_in_range(raw_goes),
                                '# of GOEs w/in Range +/- 1': num_goes_1(raw_goes),
                                'GOE % w/in Range': str(round(num_goes_in_range(raw_goes)/num_goes*100)) + '%',
                                'GOE % w/in Range +/- 1': str(round(num_goes_1(raw_goes)/num_goes*100)) + '%',
                                'Panel Component Range': None,
                                'Composition': str(min_comp[0]) + ' - ' + str(max_comp[0]),
                                'Presentation': str(min_comp[1]) + ' - ' + str(max_comp[1]),
                                'Skating Skills': str(min_comp[2]) + ' - ' + str(max_comp[2]),
                                'TJ Component Range': None,
                                'TJ Composition': raw_comp[judge_id][0],
                                'TJ Presentation': raw_comp[judge_id][1],
                                'TJ Skating Skills': raw_comp[judge_id][2],
                                '# of Components': 3,
                                'Total # Components': num_total_comp,
                                '# Components w/in Range': num_comp_in_range(raw_comp),
                                '# of Components w/in Range +/- .25': num_comp_25(raw_comp),
                                'Component % w/in Range': str(round(num_comp_in_range(raw_comp)/3*100)) + '%',
                                'Component % w/in Range +/- .25': str(round(num_comp_25(raw_comp)/3*100)) + '%'},
                                index=[0])
            
            stats = pd.concat([stats, result])


        stats = stats.reset_index().drop(columns = 'index')
        stats.insert(0, "Number of Skaters", None)
        stats.insert(0, "Skater", names)

        # Summary row
        summary = pd.DataFrame({"Skater": 'Event Totals',
                            "Number of Skaters": None,
                            "Number of Judges": None,
                            "# of GOEs": sum(stats['# of GOEs']),
                            'Total # GOEs': sum(stats['Total # GOEs']),
                            '# of GOEs w/in Range': sum(stats['# of GOEs w/in Range']),
                            '# of GOEs w/in Range +/- 1': sum(stats['# of GOEs w/in Range +/- 1']),
                            'GOE % w/in Range': str(round(sum(stats['# of GOEs w/in Range'])/sum(stats['# of GOEs'])*100, 2)) + '%',
                            'GOE % w/in Range +/- 1': str(round(sum(stats['# of GOEs w/in Range +/- 1'])/sum(stats['# of GOEs'])*100, 2)) + '%',
                            'Panel Component Range': None,
                            'Composition': None,
                            'Presentation': None,
                            'Skating Skills': None,
                            'TJ Component Range': None,
                            'TJ Composition': None,
                            'TJ Presentation': None,
                            'TJ Skating Skills': None,
                            '# of Components': sum(stats['# of Components']),
                            'Total # Components': sum(stats['Total # Components']),
                            '# Components w/in Range': sum(stats['# Components w/in Range']),
                            '# of Components w/in Range +/- .25': sum(stats['# of Components w/in Range +/- .25']),
                            'Component % w/in Range': str(round(sum(stats['# Components w/in Range'])/sum(stats['# of Components'])*100, 2)) + '%',
                            'Component % w/in Range +/- .25': str(round(sum(stats['# of Components w/in Range +/- .25'])/sum(stats['# of Components'])*100, 2)) + '%'},
                            index=[0])
            
        stats = pd.concat([stats, summary])

        # Create an empty Series with the same columns as the DataFrame and name of event listed
        event_name = S_det.find('h2', {"class":"catseg"}).text
        empty_row = pd.Series([None] * len(df.columns), index=df.columns) 
        empty_row["Skater"] = event_name
        empty_row["Number of Skaters"] = len(stats) - 1
        
        # Insert the empty row at index 0 (first position)
        stats = pd.concat([stats.iloc[:0], pd.DataFrame([empty_row]), stats.iloc[0:]]).reset_index(drop=True)
        
        print(stats)
        
        final = pd.concat([final, stats])
        final = final.append(pd.Series([None] * len(df.columns), index=df.columns), ignore_index = True)
        found = False


final.to_csv("test_html.csv", index=False)

