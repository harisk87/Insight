#Extracts features from yelp html page and inserts them into SQL database
#Lots of restaurants are skipped if yelp page doesn't have full information
import re
import string

from lxml import etree
from bs4 import BeautifulSoup
from nltk.corpus import stopwords
import nltk

import redis_database
import sql_database

redis_db = redis_database.RedisDatabase()
exclude = set(string.punctuation)
sw = set(stopwords.words('english'))
stemmer = nltk.PorterStemmer()

def get_address(spans, itemprop):
    address = ""
    addresses = [span.contents for span in spans if span.get("itemprop")==itemprop]
    if len(addresses)>0:
        address = addresses[0][0]
    return address

def get_reviews(soup):
    texts = []

    lis = soup.find_all("li")
    reviews = [li for li in lis if li.get("class") and li.get("class")[0]=='review']

#    from code import interact; interact(local=locals())
    for i, review in enumerate(reviews):
        ps = review.find_all("p")
        comment = [p for p in ps if p['class'][0]=='review_comment']
        #Catches reviews without any words
        if len(comment)>0:
            texts.append(" ".join(comment[0].stripped_strings))
        #texts.append(" ".join(([p for p in ps if p['class'][0]=='review_comment'][0].stripped_strings)))
    return texts

def clean_review(reviews):
    review = " ".join(reviews)
    review = ''.join(ch for ch in review if ch not in exclude).lower()
    review = review.split()
    #review = [stemmer.stem(word) for word in review if word not in sw]
    review = [word for word in review if word not in sw]
    return " ".join(review)

def main():
    redis_db = redis_database.RedisDatabase()

    attrs = ['Alcohol', 'HasTV', 'NoiseLevel', 'RestaurantsAttire', 'BusinessAcceptsCreditCards', 'Ambience', 'RestaurantsGoodForGroups', 'Caters', 'WiFi', 'RestaurantsReservations', 'RestaurantsTakeOut', 'GoodForKids', 'WheelchairAccessible', 'RestaurantsTableService', 'OutdoorSeating', 'RestaurantsPriceRange2', 'RestaurantsDelivery', 'GoodForMeal', 'BusinessParking']

    db = sql_database.DbAccess('YELP', usr='root')
    db.cursor.execute('DROP TABLE IF EXISTS Restaurant;')

    Columns = 'Name CHAR(80), Street CHAR(80), City CHAR(40), State CHAR(10), Zip CHAR(10), FullName CHAR(200), Site CHAR(100), Rating FLOAT, Favorites CHAR(200)'
    Columns += ', RestaurantType CHAR(200), Latitude FLOAT, Longitude FLOAT, SimilarRest1 CHAR(100), SimilarRest2 CHAR(100), SimilarRest3 CHAR(100), NReviews INT, Review LONGTEXT'

    for attr in attrs:
        Columns += ', ' + attr + ' CHAR(80)'

    db.cursor.execute('CREATE TABLE Restaurant (' + Columns + ');')

    count = 0
    for restaurant in redis_db.get_members("restaurant_searched"):
        count += 1
        if count%100==0:
            print count
            db.commit()

        rest_info = redis_db.get_info(restaurant)
        n_reviews = rest_info['reviews']

        if n_reviews < 40:
            continue

        page = rest_info['yelp_page']
        soup = BeautifulSoup(page)
        divs = soup.find_all('div')

        new_info = {}

        bizRating = [div for div in divs if div.get("id")=="bizRating"]

        #Fails if page essentially has no info
        if len(bizRating)==0:
            continue
        new_info["Rating"] = bizRating[0].meta['content']

        #Get name and address
        h1s = soup.find_all("h1")
        name = h1s[0].contents[0].strip()

        #Just skip restaurants with chinese characters in their name because I don't feel like dealing with the encoding right now
        if re.findall(ur'[\u4e00-\u9fff]+',name):
            continue
        spans = soup.find_all('span')

        street = get_address(spans, "streetAddress")
        city = get_address(spans, "addressLocality")
        state = get_address(spans, "addressRegion")
        zipcode = get_address(spans, "postalCode")
        full_name = name + ' ' + street + ' ' + city + ', ' + state + ' ' + zipcode

        #Get ngrams from snippets
        review_snippets = [div for div in divs if div.get("class") and len(div.get("class"))>1 and div.get("class")[0]=="media-story" and div.get("class")[1]=="snippet"]
        ngrams = []
        for snippet in review_snippets:
            for object in snippet.contents:
                try:
                    if object.get('ngram'):
                        ngrams.append(object.get('ngram'))
                except:
                    continue
        new_info["Ngrams"] = ngrams

        #Grab all the header data on the restaurant page
        dds = soup.find_all('dd')
        for dd in dds:
            content = dd.contents[0]
            #Convert price to a scale from 1 to 4
            if dd['class'][0].find("RestaurantsPriceRange2")!=-1:
                content = 4 - len(dd.span.span['data-remainder'])
            new_info[str(dd['class'][0])[5:]] = str(content)

        #Get the type of the restaurant
        bizInfo = [div for div in divs if div.get("id")=="bizInfoContent"]
        bizInfo_spans = bizInfo[0].find_all('span')
        category = [span for span in bizInfo_spans if span.get('id')=="cat_display"][0]
        restaurant_type = [content.contents[0].lstrip() for content in category.contents if hasattr(content, 'contents')]
        new_info['restaurant_type'] = restaurant_type

        #Get similar restaurants
        rec_bizs = [div for div in divs if div.get("id")=="bizSimilarBox"]
        if len(rec_bizs)==0:
            continue
        rec_links = [rec_biz['href'] for rec_biz in rec_bizs[0].find_all('a') if str(rec_biz.get("id")).find("bizreclink")!=-1]
        if len(rec_links)<3:
            continue


        #Get latitude and longitude from map
        imgs = soup.find_all('img')
        if len([img['src'] for img in imgs if img.get("alt")=="Map of Business"])==0:
            continue
        map = [img['src'] for img in imgs if img.get("alt")=="Map of Business"][0]
        lat = map[map.find('center')+7:map.find("%2C")]
        long = map[map.find('%2C')+3:map.find("&language")]

        review = clean_review(get_reviews(soup)).encode('ascii',errors='ignore')

        for i in range(1,5):
            if ('yelp_page'+str(i)) in rest_info:
                page = rest_info['yelp_page'+str(i)]
                soup = BeautifulSoup(page)
                review += clean_review(get_reviews(soup)).encode('ascii',errors='ignore')

        #Now insert all this information into SQL
        Values = 'INSERT INTO Restaurant (Name, Street, City, State, Zip, FullName, Site, Rating, Favorites, RestaurantType'
        Values += ', Latitude, Longitude, SimilarRest1, SimilarRest2, SimilarRest3, NReviews, Review'
        for attr in attrs:
            Values += ', ' + attr
        
        Values += ') VALUES ("' + name + '", "' + street + '", "' + city + '", "' + state + '", "' + zipcode + '", "' + full_name + '", "' + restaurant + '", '
        Values += bizRating[0].meta["content"] + ', "' + "---".join(ngrams) + '", "' + "---".join(restaurant_type) + '", '
        Values += lat.encode('utf-8') + ', ' +  long.encode('utf-8') + ', "'
        Values += rec_links[0].encode('utf-8') + '", "' + rec_links[1].encode('utf-8') + '", "' + rec_links[2].encode('utf-8')  + '", '
        Values += str(n_reviews) + ', "' + review

        for attr in attrs:
            if attr not in new_info:
                new_info[attr] = "NULL";
            Values += '", "' + new_info[attr]

        Values += '");'

        for attr in attrs:
            Columns += ', ' + attr + ' CHAR(80)'

        Values += ';'
        db.cursor.execute(Values)

    db.commit()
    db.close()

if __name__ == '__main__':
    main()
