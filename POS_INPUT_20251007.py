#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
@author: rebeccaye
"""

import pandas as pd
import numpy as np
import os


"""
Verify that python file and quickbooks data are in same os.getcwd()
"""


# read file from QuickBooks
print("Double check that quickbooks excel sheet is named 'Sheet1'.")
from_quickbooks = input("Enter QuickBooks file name '__'.xlsx: ")
quickbooks_file = from_quickbooks + ".xlsx"

# Get the current working directory
current_dir = os.getcwd()

# Construct the path to the parent directory
parent_dir = os.path.abspath(os.path.join(current_dir, os.pardir))

# extract data, create Pandas DataFrame
direct = pd.read_excel(quickbooks_file, 'Sheet1')
df = pd.DataFrame(direct)

# remove blank space columns, remove Type and Balance columns
df = df.drop(df.columns[0],axis=1)
df = df.dropna(axis=1, how='all')
df = df.drop(columns=['Type', 'Balance'])

# rename columns, reconfigure Date column to date object
df = df.rename(columns={'Memo': 'Description', 'Unnamed: 1': 'Full_REP',
                        'Ship To State': 'State', 'Ship To City': 'City'})
df['Date'] = df['Date'].dt.date

# fill in Full_REP row for all reps, add REP column with rates
df['Full_REP'] = df['Full_REP'].ffill()
df['REP'] = np.nan

# drop non-sales rows
df.dropna(subset=['Date'], inplace=True)

# duplicate and split Brandon/Tenn rows
import copy
rows_to_split = copy.deepcopy(df[df['Full_REP'] == 'Brandon/Tenn Tech (NC/SC)'])
rows_to_split['Full_REP'] = 'Tenn Tech, LLC'
rows_to_split['REP'] = 'BRANDON 7%/TENN TECH 7%'
df = pd.concat([df, rows_to_split], ignore_index=True)
df.loc[df['Full_REP'] == 'Brandon/Tenn Tech (NC/SC)', 'REP'] = 'BRANDON 7%/TENN TECH 7%'
df.loc[df['Full_REP'] == 'Brandon/Tenn Tech (NC/SC)', 'Full_REP'] = 'Brandon Associates, Inc.'


# add column indicating direct versus distributor commission type
df['Commission Type'] = 'DIRECT'




def filter_dir_rows(row):

        # Remove shipping and tariffs
        return (not str(row['Item']).startswith('Shipping')
                and not str(row['Item']).startswith('Tariff')
                and not (str(row['Name']).startswith('Zzipco') 
                        and (str(row['State']) == 'FL')) # Remove Brandon Florida
                and not (str(row['Name']).startswith('Radwell') 
                         and (str(row['State']) == 'TX'))) # Remove Radwell Texas

# execute filter
df = df[df.apply(filter_dir_rows, axis=1)]



"""
PART 1: Compute Direct Commission Rates
"""

# Read commission rules
rules_df = pd.read_excel('direct_comm_rules.xlsx')
rules_df.columns = rules_df.iloc[0]   # set row 0 as column names
rules_df = rules_df[1:]               # drop the original first row from data
rules_df.reset_index(drop=True, inplace=True)  # reset the index

# initialize split commission
split_comm = []

# Helper function to apply commission rules row-by-row
def compute_commission(row):
    for i, rule in rules_df.iterrows():
        if rule['rep_name'] not in str(row['Full_REP']):
            continue
        if pd.notna(rule['customer_keyword']):
            customers = [c.strip() for c in rule['customer_keyword'].split(',')]
            if not any(c in str(row['Name']) for c in customers):
                continue
        if pd.notna(rule['state_list']):
            states = [s.strip() for s in rule['state_list'].split(',')]
            if str(row['State']) not in states:
                continue
        if pd.notna(rule['item_prefix']):
            items = [i.strip() for i in rule['item_prefix'].split(',')]
            if not any(str(row['Item']).startswith(i) for i in items):
                continue
        if pd.notna(rule['city_keyword']) and rule['city_keyword'] not in str(row['City']):
            continue
        if pd.notna(rule['split']):
            reps = [r.split(' ')[0] for r in rule['commission_rate'].split('/')]
            for r in reps:
                if r not in str(row['Full_REP']).upper():
                    split_row = row
                    split_row['Full_REP'] = r.split(' ')[0]
                    split_row['REP'] = rule['commission_rate']
                    split_comm.append(split_row.to_frame().T)
                    
        
        # print(f"Matched rule: {rule['customer_keyword']} → {rule['commission_rate']}")
        return rule['commission_rate']  # first match wins
    
    # Default if no rule matched
    return str(row['Full_REP']).split(' ')[0].upper() + ' 10%'


# Compute rates and save in new dataframe variable
dir_df = df
dir_df.loc[dir_df['REP'].isna(), 'REP'] = dir_df.apply(compute_commission, axis=1)

# Add split commissions for each rep and re-sort
split_df = pd.concat(split_comm, ignore_index=True)
dir_df = pd.concat([df, split_df], ignore_index=True)
dir_df = dir_df.sort_values(by=['Full_REP', 'REP', 'Date'], ascending=True)

# Helper function to get exact rate
def find_rate(row):
    breakdown = str(row['REP']).split('/')
    for x in breakdown:
        if x.split(' ')[0] in str(row['Full_REP']).upper():
            return x.split(' ')[-1]
    return None

# Apply find_rate
dir_df['Rate'] = dir_df.apply(find_rate, axis=1)

# Compute Commission amounts
dir_df['Commission'] = dir_df['Amount'].astype('float') * \
                    dir_df['Rate'].str.rstrip('%').astype('float')/100
dir_df['Num'] = dir_df['Num'].astype('float')

# Re-order and round to 2 decimal places

dir_df['Commission'] = dir_df['Commission'].round(2)
dir_df.rename(columns={'Full_REP': 'REP', 'REP': 'REP Rates', 'Ship Zip': 'Zip code'}, inplace=True)
ordered_col = ['REP', 'Date', 'Num', 'Name', 'City', 'State', 'Zip code',
               'Item', 'Description', 'Qty', 'Sales Price', 'Amount',
               'Rate', 'Commission', 'REP Rates', 'Commission Type']
dir_df['REP'] = dir_df['REP'].str.split(' ').str[0].str.upper()

dir_df = dir_df[ordered_col]

# Publish mastercopy of direct commissions to Excel
dir_master_path = os.path.join(parent_dir, from_quickbooks + '/mastercopy_direct.xlsx')
dir_df.to_excel(dir_master_path, index=False)


"""
PART 2: Distributor Commission mastercopy
"""

# Carlton Bates reformatting
distributor = input("Enter Carlton Bates file name '__'.txt: ")
distributor = distributor + '.txt'
cbates = pd.read_csv(distributor)

ll = []

with open(distributor, 'r') as cbates:
    content = cbates.readlines()
    for line in content:
        l = line.strip().split(',')
        l = [i.strip('"') for i in l]
        ll.append(l)
    table = ll[1:-1]

    dist = pd.DataFrame(table)
    dist.drop(columns=dist.columns[0],
               axis=1,
               inplace=True)
    total = []
    new_col = ['Office', 'City', 'State', 'Zip code', 'Item', 'Qty', 'PO', 'Date', 'Sales Price', 'Seller Price',
               'Seller Total', 'Customer PO', 'Name', 'ID', 'Date2', 'Extra']
    dist.columns = new_col
    dist = dist.drop(columns=['Office', 'PO', 'Seller Price', 'Seller Total',
                                'Customer PO', 'ID', 'Date2', 'Extra'])
    dist['Sales Price'] = dist['Sales Price'].astype('float').round(2)

    from datetime import datetime
    dist['Date'] = dist['Date'].apply(lambda x:
                                        [int(x[i:i+2]) for i in range(0, 6, 2)])
    dist['Date'] = dist['Date'].apply(lambda x:
                                        [2000 + x[0], x[1], x[2]])
    dist['Date'] = dist['Date'].apply(lambda x:
                                        datetime(x[0], x[1], x[2])).dt.date

# Override select sales prices
dist.loc[dist['Item'].str.contains(r'HLS-1A-04A-FS\(R'), 'Sales Price'] = 31
dist.loc[dist['Item'].str.contains('SXP318H-N'), 'Sales Price'] = 2.6

# Upload distributor rules xlsx
rules = pd.read_excel("dist_comm_rules.xlsx")

def assign_rep(row):
    for i, r in rules.sort_values("priority").iterrows():

        if pd.notna(r['name_keyword']) and r['name_keyword'] not in str(row['Name']):
            continue

        if pd.notna(r['item_keyword']):
            items = [i.strip() for i in r['item_keyword'].split(',')]
            if not any(i in str(row['Item']) for i in items):
                continue

        if pd.notna(r['state_list']):
            states = [s.strip() for s in r['state_list'].split(',')]
            if row['State'] not in states:
                continue
        
        # Optional sales price override
        if pd.notna(r['sales_price']):
            row['Sales Price'] = float(r['sales_price'])

        return r['rep_assign']
    
    return "NO 10%"  # default if nothing matches

dist['REP Rates'] = dist.apply(assign_rep, axis=1)

# Purchase total
dist['Amount'] = dist['Qty'].astype(
    'float')*dist['Sales Price'].astype('float')
dist['Commission Type'] = 'DIST'
dist['REP'] = None
ordered_dist_col = ['REP', 'City', 'State', 'Zip code', 'Item', 'Qty', 'Date', 'Sales Price',
       'Name', 'REP Rates', 'Amount', 'Commission Type']
dist = dist[ordered_dist_col]
dist = dist.sort_values(by = ['REP Rates','Date'], ignore_index=True)

dist_master_path = os.path.join(parent_dir, from_quickbooks + '/mastercopy_dist.xlsx')
dist.to_excel(dist_master_path, index=False)
 

"""
PART 3: Generate rep files by matching distributor and direct commissions
"""

all_reps = ['ALLIED', 'BRANDON', 'ELOTEK', 'EMT', 'HILL', 'LUSCOMBE', 'MEL',
            'RAY', 'PAYPAL','SYNERGISTIC', 'TENN', 'NO']

rep_list = dir_df['REP'].unique()
print(rep_list)

# Helper function to get exact dist rate
def find_dist_rate(row):
    breakdown = str(row['REP Rates']).split('/')
    for x in breakdown:
        if x.split(' ')[0] in rep:
            return x.split(' ')[-1]
    return None

    
for rep in all_reps:
    # Direct section
    direct_df = copy.deepcopy(dir_df.loc[dir_df['REP'].str.contains(rep, na=False)])
    
    if not direct_df.empty:
        # clear rep name column
        direct_df.loc[:,'REP'] = np.nan
        # Title row with rep name
        title_df = pd.DataFrame([[None] * len(direct_df.columns)], columns = direct_df.columns)
        title_df.loc[:,'REP'] = title_df['REP'].astype('string')
        title_df.loc[0,'REP'] = 'Direct Commission'
        direct_df = pd.concat([title_df.dropna(axis=1, how='all'), direct_df], ignore_index=True)
        
    # Distributor section
    dist_df = copy.deepcopy(dist.loc[dist['REP Rates'].str.contains(rep, na=False)])
    
    if not dist_df.empty:
        # Apply find_rate
        dist_df.loc[:,'Rate'] = dist_df.apply(find_dist_rate, axis=1)
        
        # Compute commissions
        dist_df.loc[:,'Commission'] = dist_df['Amount'].astype('float') * \
                                dist_df['Rate'].astype('string').str.rstrip('%').astype('float')/100
        dist_df.loc[:,'Commission'] = dist_df['Commission'].round(2)
        
        # sort
        dist_upper = dist_df[~dist_df['REP Rates'].str.contains(
            '/')].sort_values(by=['REP Rates','Date'], ignore_index=True)
    
        dist_lower = dist_df[dist_df['REP Rates'].str.contains('/')].\
            sort_values(by=['REP Rates', 'Date'])
    
        dist_df = pd.concat([dist_upper.dropna(axis=1, how='all'), \
                             dist_lower.dropna(axis=1, how='all')],
                            axis=0, ignore_index=True)
    
        # Repair Zip and Qty formatting
        dist_df.loc[:,'Zip code'] = dist_df['Zip code'].astype('float')
        dist_df.loc[:,'Qty'] = dist_df['Qty'].str.strip(' ').astype('Int64')
    
        # format spaces between different commission rates
        rep_split = []
    
        switches = dist_df['REP Rates'].ne(dist_df['REP Rates'].shift(-1))
        idx = switches[switches].index
    
        dist_df_new = pd.DataFrame(index=idx + 0.5)
        dist_df = pd.concat([dist_df, dist_df_new]).sort_index()
        dist_df = dist_df.reset_index(drop=True)
    
        first_row = pd.DataFrame(columns=dist_df.columns)
        first_row.loc[0, 'REP'] = 'Distributor Commission'
        dist_df = pd.concat([first_row.dropna(axis=1, how='all'), dist_df],
                            axis=0, ignore_index=True)
    
    
    # Attach to same DataFrame
    final = pd.concat([direct_df.dropna(axis=1, how='all'), \
                       dist_df.dropna(axis=1, how='all')], axis=0, ignore_index=True)
    
    if not final.empty:
        # Re-order columns
        
        # Eliminate descriptions for all non-Brandon reps
        if rep != 'BRANDON':
            if 'Description' in final.columns:
                final = final.drop(columns=['Description'])
            

        # Format price columns
        cols = ['Sales Price', 'Amount', 'Commission']
        final = final.rename(columns = {'Sales Price' : '($) Sales Price',
                                        'Amount' : '($) Amount',
                                        'Commission' : '($) Commission'})
        
        # Format item column
        final['Item'] = final['Item'].apply(lambda x:
                                            str(x).split(' ')[0] if str(x) != 'nan' else None)
            
        # Total Commission
        sum_cmsn = final['($) Commission'].sum()
        total_cmsn = pd.DataFrame({'($) Amount': ['Total Commission:'],
                                  '($) Commission': [sum_cmsn]})
        final = pd.concat([final, total_cmsn], axis=0, ignore_index=True)

        
    # Export to excel
    file_name = os.path.join(parent_dir, from_quickbooks + '/' + rep + ' ' + quickbooks_file)
    final.to_excel(file_name, index=False)
    
# Find SUNS BELT
BELT_dir = copy.deepcopy(dir_df.loc[
                (dir_df['State'].isin(['TX', 'OK', 'AR', 'LA'])) & \
                (~dir_df['Name'].str.contains('Amazon', na=False)) & \
                (~dir_df['Name'].str.contains('Ebay', na=False))
                ])
BELT_dist = copy.deepcopy(dist.loc[
                (dist['State'].isin(['TX', 'OK', 'AR', 'LA']))
                ])
BELT_dist = BELT_dist.sort_values(by=['REP Rates', 'Date'])
suns_belt = pd.concat([BELT_dir.dropna(axis=1, how='all'), \
                       BELT_dist.dropna(axis=1, how='all')], axis=0, ignore_index=True)

file_name = os.path.join(parent_dir, from_quickbooks + '/SUNSBELT.xlsx')
suns_belt.to_excel(file_name, index=False)

print('DONE.')